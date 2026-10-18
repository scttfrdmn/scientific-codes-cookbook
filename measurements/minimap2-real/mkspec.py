#!/usr/bin/env python3
"""Write the minimap2-real TaskSpecs (arm64; x86 and AMD come from scripts/spec_arch.py).

    python3 measurements/minimap2-real/mkspec.py     # rewrites the six *.task.json beside it

prep         (samtools, once)  no-alt reference + pbmm2's chr20 placements + input sha256s
align        (minimap2)        align.sh: the chr20 reads vs the whole genome -- the sizing probe
prep-genome  (samtools, once)  the whole deposit as 25 FASTQ chunks + pbmm2's placement of each read
genome       (minimap2)        genome.sh: the whole sample, conservation + pbmm2 agreement, per chip
sort         (samtools)        sort + index the reads placed on chr20; records conserved, depth
truth        (bcftools)        genotype the GIAB v4.2.1 chr20 truth SNVs from them
"""
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
B = "s3://${COOKBOOK_BUCKET}"
I = B + "/inputs/minimap2-real/"
R = B + "/runs/minimap2-real/"
G = R + "genome/"
NOALT = "GRCh38_no_alt_analysis_set.fa"
FULL = "GRCh38_full_analysis_set_plus_decoy_hla.fa"
SAMTOOLS = "quay.io/aarchbio/samtools@sha256:1191739637fb6f46ef97c02b28f693b25ca3ca61f90e1337f349b7b7cc0be4f7"
MINIMAP2 = "quay.io/aarchbio/minimap2@sha256:ef4a5fb788815f5f9fd88544affa6764b5dacfc425aaf249a4adcd51416c041a"
BCFTOOLS = "quay.io/aarchbio/bcftools@sha256:8171fe74464620a0585cc8998fd9bacbfc04480ac5571229f22f390ecfd5658e"

HEAD = """set +e
set -uo pipefail
# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).
export PATH=/opt/conda/bin:$PATH TMPDIR=/tmp HOME=/tmp
cd /tmp
"""
PINCHK = """echo '== staged bytes must match their pins =='
grep -E '%s' pins.sha256 > pins.sel
sha256sum -c pins.sel || { echo 'FAIL: a staged input does not match its pin'; exit 1; }
"""


def sha(name):
    return hashlib.sha256(open(os.path.join(HERE, name), "rb").read()).hexdigest()


def io(src, dst):
    return {"source": src, "destination": dst}


def spec(tid, image, cmd, cpu, mem, fam, ins, outs, ttl, cap):
    return {"task_id": tid, "container": image, "command": ["bash", "-c", cmd],
            "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": [fam]},
            "inputs": ins, "outputs": outs,
            "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap}}


prep_cmd = HEAD + """echo '%s  prep.sh' | sha256sum -c || { echo 'FAIL: prep.sh does not match its pin'; exit 1; }
bash prep.sh > prep.log 2>&1
RC=$?
cat prep.log
test "$RC" = 0 || { echo 'FAIL: prep failed'; exit 1; }
grep -q 'PREP OK' prep.log || { echo 'FAIL: no completion sentinel'; exit 1; }
sha256sum truth.chr20.vcf.gz truth.chr20.bed >> prep-pins.sha256
echo DONE
""" % sha("prep.sh")
prep = spec("cookbook-minimap2real-prep", SAMTOOLS, prep_cmd, 8, 30, "m8g",
            [io(B + "/inputs/bwa-real/" + FULL, "/tmp/" + FULL),
             io(B + "/inputs/bwa-real/" + FULL + ".fai", "/tmp/" + FULL + ".fai"),
             io(B + "/inputs/minimap2/hifi.chr20.fq.gz", "/tmp/hifi.chr20.fq.gz"),
             io(B + "/inputs/gatk4/truth.chr20.vcf.gz", "/tmp/truth.chr20.vcf.gz"),
             io(B + "/inputs/gatk4/truth.chr20.bed", "/tmp/truth.chr20.bed"),
             io(I + "prep.sh", "/tmp/prep.sh")],
            [io("/tmp/" + NOALT, I + NOALT), io("/tmp/" + NOALT + ".fai", I + NOALT + ".fai"),
             io("/tmp/pbmm2.chr20.tsv", I + "pbmm2.chr20.tsv"),
             io("/tmp/prep-pins.sha256", I + "prep-pins.sha256"), io("/tmp/prep.log", I + "prep.log")],
            "40m", 0.25)

align_cmd = HEAD + PINCHK % "align.sh|no_alt_analysis_set.fa$|pbmm2|hifi" + """T0=$(date +%s)
bash align.sh > run.log 2>&1
RC=$?
echo "align.sh exit $RC in $(( $(date +%s) - T0 ))s"
cat run.log
test "$RC" = 0 || { echo 'FAIL: the workload failed'; exit 1; }
grep -q 'MINIMAP2 REAL OK' run.log || { echo 'FAIL: no completion sentinel'; exit 1; }
echo DONE
"""
align = spec("cookbook-minimap2real-align", MINIMAP2, align_cmd, 16, 64, "m8g",
             [io(I + NOALT, "/tmp/" + NOALT), io(B + "/inputs/minimap2/hifi.chr20.fq.gz", "/tmp/hifi.chr20.fq.gz"),
              io(I + "pbmm2.chr20.tsv", "/tmp/pbmm2.chr20.tsv"), io(I + "align.sh", "/tmp/align.sh"),
              io(I + "pins.sha256", "/tmp/pins.sha256")],
             [io("/tmp/" + f, R + f) for f in ["aln.sam", "results.tsv", "run.log", "minimap2.log", "cpu.txt", "samp.tsv"]],
             "3h", 1.20)

sort_cmd = HEAD + """T0=$(date +%s)
samtools sort -@ 8 -m 1G -T /tmp/st -o hifi.sorted.bam aln.sam 2> sort.log || { cat sort.log; echo 'FAIL: sort'; exit 1; }
T1=$(date +%s)
samtools index -@ 8 hifi.sorted.bam || { echo 'FAIL: index'; exit 1; }
MAGIC=$(od -An -tx1 -N4 hifi.sorted.bam | tr -d ' \\n')
QC=0; samtools quickcheck -v hifi.sorted.bam && QC=1
NSAM=$(grep -vc '^@' aln.sam)
NBAM=$(samtools view -c -@ 8 hifi.sorted.bam)
samtools coverage -r chr20 hifi.sorted.bam > cov.txt
DEPTH=$(awk 'NR==2{print $7}' cov.txt); COVPCT=$(awk 'NR==2{print $6}' cov.txt)
{
  echo "sort_wall_s     $(( T1 - T0 ))"
  echo "bam_magic       $MAGIC  want 1f8b0804"
  echo "quickcheck      $QC  want 1"
  echo "sam_records     $NSAM"
  echo "bam_records     $NBAM  want == sam_records"
  echo "chr20_depth     $DEPTH  want >=25"
  echo "chr20_covered   $COVPCT  want >=98"
  echo "bam_bytes       $(stat -c%s hifi.sorted.bam)"
} > sort-check.txt
cat sort-check.txt cov.txt
test "$MAGIC" = 1f8b0804 && test "$QC" = 1 && test "$NSAM" = "$NBAM" || { echo 'FAIL: sort check'; exit 1; }
awk -v d="$DEPTH" -v c="$COVPCT" 'BEGIN{exit !(d>=25 && c>=98)}' || { echo 'FAIL: chr20 depth/coverage'; exit 1; }
echo 'SORT CHECK PASSED'
"""
sort = spec("cookbook-minimap2real-sort", SAMTOOLS, sort_cmd, 8, 30, "m8g",
            [io(G + "chr20.sam", "/tmp/aln.sam")],
            [io("/tmp/hifi.sorted.bam", G + "hifi.sorted.bam"), io("/tmp/hifi.sorted.bam.bai", G + "hifi.sorted.bam.bai"),
             io("/tmp/sort-check.txt", G + "sort-check.txt"), io("/tmp/sort.log", G + "sort.log")],
            "40m", 0.25)

truth_cmd = HEAD + PINCHK % "no_alt_analysis_set.fa|truth" + """REF=%s
bcftools view -R truth.chr20.bed -v snps -i 'GT="alt"' -Ou truth.chr20.vcf.gz \\
  | bcftools norm -f $REF -m -any -Oz -o t.snv.vcf.gz || { echo 'FAIL: truth norm'; exit 1; }
bcftools index -f t.snv.vcf.gz
bcftools view -H t.snv.vcf.gz | awk '{print $1"\\t"$2}' > pos.tsv
NTRUTH=$(wc -l < pos.tsv)
# Genotype ONLY at the truth sites: recall of known variants is a check on placement, not a
# calling benchmark -- looking only where the answer is cannot measure precision.
T0=$(date +%%s)
bcftools mpileup -f $REF -T pos.tsv -q 20 -Ou hifi.sorted.bam 2> mp.log \\
  | bcftools call -m -Oz -o q.vcf.gz || { tail mp.log; echo 'FAIL: mpileup/call'; exit 1; }
T1=$(date +%%s)
bcftools index -f q.vcf.gz
bcftools view -v snps -i 'GT="alt"' -Ou q.vcf.gz | bcftools norm -f $REF -m -any -Oz -o q.snv.vcf.gz
bcftools index -f q.snv.vcf.gz
rm -rf is; bcftools isec -p is q.snv.vcf.gz t.snv.vcf.gz || { echo 'FAIL: isec'; exit 1; }
TP=$(grep -vc '^#' is/0002.vcf); FN=$(grep -vc '^#' is/0001.vcf)
REC=$(awk -v tp="$TP" -v fn="$FN" 'BEGIN{printf "%%.5f", tp/(tp+fn)}')
{
  echo "mpileup_wall_s      $(( T1 - T0 ))"
  echo "truth_snv_sites     $NTRUTH"
  echo "recovered (TP)      $TP"
  echo "missed (FN)         $FN"
  echo "truth_snv_recall    $REC  want >=0.95"
} > truth-support.txt
cat truth-support.txt
awk -v v="$REC" 'BEGIN{exit !(v>=0.95)}' || { echo 'FAIL: recall'; exit 1; }
echo 'TRUTH SUPPORT CHECK PASSED'
""" % NOALT
truth = spec("cookbook-minimap2real-truth", BCFTOOLS, truth_cmd, 8, 30, "m8g",
             [io(I + NOALT, "/tmp/" + NOALT), io(I + NOALT + ".fai", "/tmp/" + NOALT + ".fai"),
              io(B + "/inputs/gatk4/truth.chr20.vcf.gz", "/tmp/truth.chr20.vcf.gz"),
              io(B + "/inputs/gatk4/truth.chr20.vcf.gz.tbi", "/tmp/truth.chr20.vcf.gz.tbi"),
              io(B + "/inputs/gatk4/truth.chr20.bed", "/tmp/truth.chr20.bed"),
              io(G + "hifi.sorted.bam", "/tmp/hifi.sorted.bam"), io(G + "hifi.sorted.bam.bai", "/tmp/hifi.sorted.bam.bai"),
              io(I + "pins.sha256", "/tmp/pins.sha256")],
             [io("/tmp/truth-support.txt", G + "truth-support.txt"), io("/tmp/mp.log", G + "mpileup.log")],
             "40m", 0.25)

prepg_cmd = HEAD + """echo '%s  prep-genome.sh' | sha256sum -c || { echo 'FAIL: prep-genome.sh does not match its pin'; exit 1; }
echo '%s  prep-chunk.sh' | sha256sum -c || { echo 'FAIL: prep-chunk.sh does not match its pin'; exit 1; }
bash prep-genome.sh > prep-genome.log 2>&1
RC=$?
cat prep-genome.log
test "$RC" = 0 || { echo 'FAIL: prep failed'; exit 1; }
grep -q 'PREP GENOME OK' prep-genome.log || { echo 'FAIL: no completion sentinel'; exit 1; }
echo DONE
""" % (sha("prep-genome.sh"), sha("prep-chunk.sh"))
prepg = spec("cookbook-minimap2real-prepgenome", SAMTOOLS, prepg_cmd, 32, 256, "r8g",
             [io(I + NOALT + ".fai", "/tmp/" + NOALT + ".fai"), io(I + "pbmm2.chr20.tsv", "/tmp/pbmm2.chr20.tsv"),
              io(B + "/inputs/minimap2/hifi.chr20.fq.gz", "/tmp/hifi.chr20.fq.gz"),
              io(I + "prep-genome.sh", "/tmp/prep-genome.sh"), io(I + "prep-chunk.sh", "/tmp/prep-chunk.sh")],
             [io("/tmp/wg/", I + "wg/"), io("/tmp/wg-pins.sha256", I + "wg-pins.sha256"),
              io("/tmp/prep-genome.log", I + "prep-genome.log"), io("/tmp/chunks.log", I + "prep-chunks.log")],
             "3h", 3.00)

genome_cmd = HEAD + """export THREADS=32
echo '== staged bytes must match their pins =='
grep -E 'genome.sh|no_alt_analysis_set.fa$' pins.sha256 > pins.sel
cat wg-pins.sha256 >> pins.sel
sha256sum -c pins.sel > pins.check 2>&1 || { grep -v ': OK$' pins.check; echo 'FAIL: a staged input does not match its pin'; exit 1; }
echo "$(wc -l < pins.sel) files match their pins"
T0=$(date +%s)
bash genome.sh > run.log 2>&1
RC=$?
echo "genome.sh exit $RC in $(( $(date +%s) - T0 ))s"
cat run.log
test "$RC" = 0 || { echo 'FAIL: the workload failed'; exit 1; }
grep -q 'MINIMAP2 GENOME OK' run.log || { echo 'FAIL: no completion sentinel'; exit 1; }
echo DONE
"""
genome = spec("cookbook-minimap2real-genome", MINIMAP2, genome_cmd, 32, 256, "r8g",
              [io(I + NOALT, "/tmp/" + NOALT), io(I + "wg/", "/tmp/wg/"), io(I + "wg-pins.sha256", "/tmp/wg-pins.sha256"),
               io(I + "genome.sh", "/tmp/genome.sh"), io(I + "pins.sha256", "/tmp/pins.sha256")],
              [io("/tmp/" + f, G + f) for f in ["chr20.sam", "results.tsv", "chunks.tsv", "body.md5", "run.log",
                                                "minimap2.log", "index.log", "cpu.txt", "samp.tsv"]],
              "4h", 4.00)

# chr4 is the one chunk whose SAM bytes differ across chips. diff-arm64 realigns it on Graviton4
# (a repeat of the genome run's chunk) and keeps the records minus SEQ/QUAL; diff-cmp, run with
# ARCH=x86_64, realigns it there and lists the record pairs that differ.
diag_cmd = HEAD + """echo '%s  chr4-diff.sh' | sha256sum -c || { echo 'FAIL: chr4-diff.sh does not match its pin'; exit 1; }
grep -E 'wg/chr4.fq.gz' wg-pins.sha256 > p.sel; sha256sum -c p.sel ||{ echo 'FAIL: chr4.fq.gz pin'; exit 1; }
bash chr4-diff.sh > diag.log 2>&1
RC=$?
cat diag.log
test "$RC" = 0 && grep -q 'DIAG OK' diag.log || { echo 'FAIL: diag'; exit 1; }
touch diff.txt
""" % sha("chr4-diff.sh")
DIN = [io(I + NOALT, "/tmp/" + NOALT), io(I + "wg/chr4.fq.gz", "/tmp/wg/chr4.fq.gz"),
       io(I + "wg-pins.sha256", "/tmp/wg-pins.sha256"), io(I + "chr4-diff.sh", "/tmp/chr4-diff.sh")]
diff_arm = spec("cookbook-minimap2real-chr4diff", MINIMAP2, diag_cmd, 32, 64, "c8g", DIN,
                [io("/tmp/chr4.nosq.sam.gz", I + "chr4.arm64.nosq.sam.gz"), io("/tmp/diag.log", R + "chr4diff/diag.log")],
                "1h", 0.60)
diff_cmp = spec("cookbook-minimap2real-chr4cmp", MINIMAP2, diag_cmd, 32, 64, "c8g",
                DIN + [io(I + "chr4.arm64.nosq.sam.gz", "/tmp/other.nosq.sam.gz")],
                [io("/tmp/diff.txt", R + "chr4diff/diff.txt"), io("/tmp/diag.log", R + "chr4diff/diag.log")],
                "1h", 0.60)

for name, s in [("prep", prep), ("align", align), ("prep-genome", prepg), ("genome", genome),
                ("sort", sort), ("truth", truth), ("chr4-diff-arm64", diff_arm), ("chr4-diff-cmp", diff_cmp)]:
    with open(os.path.join(HERE, name + ".task.json"), "w") as f:
        json.dump(s, f, indent=2)
        f.write("\n")
    print(name + ".task.json")
