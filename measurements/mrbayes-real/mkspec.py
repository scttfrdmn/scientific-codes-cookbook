# Write the three TaskSpecs (arm64; spec_arch.py makes the x86 ones):
#   python3 mkspec.py <dir> <ngen cap> <ttl> <cost_limit>        as run: . 5000000 12h 1.5
import json, sys, os
D = sys.argv[1]
NGEN, TTL, CAP = sys.argv[2], sys.argv[3], float(sys.argv[4])
B = "s3://${COOKBOOK_BUCKET}/inputs/mrbayes-real/"
def spec(tid, cont, cmd, inputs, outdest, ttl, cap, cpu=8, mem=16):
    return {"task_id": tid, "container": cont,
      "command": ["bash", "-c", cmd],
      "resources": {"cpu": cpu, "memory_gib": mem, "architecture": "arm64", "families": ["c8g"]},
      "inputs": inputs,
      "outputs": [{"source": "/tmp/out/", "destination": outdest}],
      "lifecycle": {"ttl": ttl, "on_complete": "terminate", "cost_limit": cap}}
pre = ("set +e\n# spawn runs task commands under bash -e; `set -uo pipefail` does not clear it (spawn#707).\ncd /tmp\n"
 "grep -E ' (%s)$' pins.sha256 > pins.sel\n[ \"$(wc -l < pins.sel)\" -ge %d ] && sha256sum -c pins.sel || { echo \"FAIL: a staged input does not match its pin\"; exit 1; }\n")
ALN = {"source": "s3://${COOKBOOK_BUCKET}/inputs/iqtree/pfam38.2_seed_alignment.fa", "destination": "/tmp/aln.fa"}
mc_in = [ALN] + [{"source": B + f, "destination": "/tmp/" + f} for f in
  ["mcmc.sh", "fasta2nexus.awk", "nexus2newick.awk", "iqout.treefile", "iqout.iqtree", "rx.raxml.bestTree", "rx.raxml.bestModel", "pins.sha256"]]
names = r"mcmc\.sh|fasta2nexus\.awk|nexus2newick\.awk|aln\.fa|iqout\.treefile|iqout\.iqtree|rx\.raxml\.bestTree|rx\.raxml\.bestModel"
MB = "quay.io/aarchbio/mrbayes@sha256:a320f21c5742641ec3db1bd6b443df81bb513589acfe1f8974640fcd1c6556ab"
IQ = "quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7"
def w(name, s):
    with open(os.path.join(D, name), "w") as f:
        json.dump(s, f, indent=2); f.write("\n")
w("00-probe.task.json", spec("cookbook-mrbayes-real-probe", MB, pre % (names, 8) + "bash mcmc.sh 20000 0\n", mc_in,
  "s3://${COOKBOOK_BUCKET}/runs/mrbayes-real-probe/", "1h", 0.3))
w("01-mcmc.task.json", spec("cookbook-mrbayes-real-mcmc", MB, pre % (names, 8) + "bash mcmc.sh %s 1\n" % NGEN, mc_in,
  "s3://${COOKBOOK_BUCKET}/runs/mrbayes-real/mcmc/", TTL, CAP))
cn = r"compare\.sh|splits\.awk|aln\.fa|iqout\.treefile|iqout\.iqtree|rx\.raxml\.bestTree|rx\.raxml\.bestModel|rx\.raxml\.log"
c_in = [ALN] + [{"source": B + f, "destination": "/tmp/" + f} for f in
  ["compare.sh", "splits.awk", "iqout.treefile", "iqout.iqtree", "rx.raxml.bestTree", "rx.raxml.bestModel", "rx.raxml.log", "pins.sha256"]] + \
  [{"source": "s3://${COOKBOOK_BUCKET}/runs/mrbayes-real/mcmc/score.tsv", "destination": "/tmp/mb.score.tsv"},
   {"source": "s3://${COOKBOOK_BUCKET}/runs/mrbayes-real/mcmc/mb.consensus.nwk", "destination": "/tmp/mb.consensus.nwk"},
   {"source": "s3://${COOKBOOK_BUCKET}/runs/mrbayes-real/mcmc/mb.tstat", "destination": "/tmp/mb.tstat"},
   {"source": "s3://${COOKBOOK_BUCKET}/runs/mrbayes-real/mcmc/mb.out", "destination": "/tmp/mb.out"},
   {"source": B + "graviton4/mb.consensus.nwk", "destination": "/tmp/g4.consensus.nwk"}]
w("02-compare.task.json", spec("cookbook-mrbayes-real-compare", IQ, pre % (cn, 8) + "bash compare.sh\n", c_in,
  "s3://${COOKBOOK_BUCKET}/runs/mrbayes-real/compare/", "30m", 0.1, cpu=2, mem=4))
