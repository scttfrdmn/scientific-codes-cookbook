#!/usr/bin/env nextflow
// nf-spawn Shape-F demonstration: a fan-out + join DAG where every process step
// runs on its OWN ephemeral EC2 instance (via the nf-spawn executor -> spawn task
// run), with data handed between steps through the S3 work dir. This is what 43
// Shape-B recipes cannot show: per-rule dispatch, cross-instance S3 handoff, a DAG
// completing on Graviton.
//
// Each stage asserts its OWN identity (fails the task if wrong). The mafft-vs-muscle
// tree topology is REPORTED as an observation, not asserted: RF between the two ML
// trees conflates the alignment-method difference with iqtree's own ML-search
// stochasticity (measured 26 between plain trees vs 4 with UFBoot, same seed), so a
// hard RF assertion would measure search noise as much as alignment difference. See
// README.
nextflow.enable.dsl = 2

params.seqs            = "s3://${System.getenv('COOKBOOK_BUCKET')}/inputs/mafft-muscle/pfam_unaligned.fa"
params.expect_seqs     = 114
params.expect_residues = 49098

// --- fan-out: the same sequences aligned two independent ways, each on its own instance ---
process MAFFT {
    container 'quay.io/aarchbio/mafft@sha256:f23e4545b6c186ffa31ebbb0a70a051c06ff3e7dcc91853e84f6eced74fa3df9'
    input:  path seqs
    output: tuple val('mafft'), path('mafft_aln.fa')
    shell:
    '''
    mafft --retree 2 --maxiterate 0 --thread 1 !{seqs} > mafft_aln.fa
    # per-stage identity: an aligner must preserve every residue (conservation law)
    n=$(grep -c '^>' mafft_aln.fa)
    r=$(awk '!/^>/{gsub(/-/,"");t+=length($0)}END{print t}' mafft_aln.fa)
    echo "mafft seqs=$n residues=$r"
    [ "$n" -eq !{params.expect_seqs} ] || { echo "FAIL seqs $n != !{params.expect_seqs}"; exit 1; }
    [ "$r" -eq !{params.expect_residues} ] || { echo "FAIL residues $r != !{params.expect_residues}"; exit 1; }
    '''
}

process MUSCLE {
    container 'quay.io/aarchbio/muscle@sha256:ecfe0f7405a5e3e1237b93202c35bd984aab96e1a3466ef64a6fd0a3b7d5c2e4'
    input:  path seqs
    output: tuple val('muscle'), path('muscle_aln.fa')
    shell:
    '''
    muscle -align !{seqs} -output muscle_aln.fa
    n=$(grep -c '^>' muscle_aln.fa)
    r=$(awk '!/^>/{gsub(/-/,"");t+=length($0)}END{print t}' muscle_aln.fa)
    echo "muscle seqs=$n residues=$r"
    [ "$n" -eq !{params.expect_seqs} ] || { echo "FAIL seqs $n != !{params.expect_seqs}"; exit 1; }
    [ "$r" -eq !{params.expect_residues} ] || { echo "FAIL residues $r != !{params.expect_residues}"; exit 1; }
    '''
}

// --- each alignment builds an ML tree, on its own instance ---
process TREE {
    container 'quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7'
    tag "${aligner}"
    input:  tuple val(aligner), path(aln)
    output: tuple val(aligner), path("${aligner}.treefile"), path("${aligner}.iqtree")
    shell:
    '''
    iqtree3 -s !{aln} -m LG+G4 -T 4 -seed 12345 --prefix !{aligner} -quiet
    # per-stage identity: a valid ML tree with all taxa and a finite log-likelihood
    tips=$(grep -o ',' !{aligner}.treefile | wc -l | tr -d ' '); tips=$((tips+1))
    logl=$(awk -F': ' '/Log-likelihood of the tree/{print $2}' !{aligner}.iqtree | awk '{print $1}')
    echo "!{aligner} tips=$tips logL=$logl"
    [ "$tips" -eq !{params.expect_seqs} ] || { echo "FAIL tips $tips != !{params.expect_seqs}"; exit 1; }
    awk -v l="$logl" 'BEGIN{ if (l+0 < 0 && l != "") exit 0; print "FAIL logL not a finite negative number: " l; exit 1 }'
    '''
}

// --- join: report the two topologies' RF distance as an OBSERVATION (no assertion) ---
process OBSERVE_RF {
    container 'quay.io/aarchbio/iqtree@sha256:dc6d9f62d56fd1ca92bfb2a9fbd162d419d4f866de67e6e879ba0f895d2a6fb7'
    publishDir "s3://${System.getenv('COOKBOOK_BUCKET')}/runs/nf-spawn/r3", mode: 'copy'
    input:  path 'mafft.treefile'
            path 'muscle.treefile'
    output: path 'rf-observation.txt'
    shell:
    '''
    iqtree3 -rf mafft.treefile muscle.treefile 2>&1 | tee rf.log
    rf=$(awk '/Tree0/{print $2; exit}' *.rfdist 2>/dev/null || echo NA)
    {
      echo "# nf-spawn Shape-F: mafft vs muscle tree topology (OBSERVATION, not an assertion)"
      echo "robinson_foulds_distance  $rf"
      echo "# RF confounds the alignment-method difference with iqtree's ML-search"
      echo "# stochasticity (measured 26 plain vs 4 UFBoot on the SAME alignments+seed),"
      echo "# so this is reported, not asserted. The recipe's identities are per-stage:"
      echo "# mafft/muscle residue conservation + iqtree valid ML tree with finite logL."
    } > rf-observation.txt
    cat rf-observation.txt
    '''
}

workflow {
    // Fail here rather than let the DAG succeed in the wrong place. AWS_REGION is where
    // nf-spawn launches the instances; COOKBOOK_BUCKET holds the S3 work dir they hand data
    // through. If the two disagree the run still goes green — measured: five stages, every
    // .exitcode 0, the right RF — while every intermediate crosses the continent, and nothing
    // in Nextflow's summary or spawn's completion records mentions it.
    if (!System.getenv('COOKBOOK_BUCKET')) {
        error "COOKBOOK_BUCKET is unset — run: export COOKBOOK_BUCKET=\$(make -C ../.. print-bucket)"
    }
    if (!(System.getenv('AWS_REGION') ?: System.getenv('AWS_DEFAULT_REGION'))) {
        error "AWS_REGION is unset — run: export AWS_REGION=\$(aws configure get region). " +
              "It must be the region holding COOKBOOK_BUCKET, or every stage handoff crosses regions."
    }
    seqs = Channel.fromPath(params.seqs)
    // fan-out
    aln = MAFFT(seqs).mix(MUSCLE(seqs))
    // per-alignment tree (2 instances)
    trees = TREE(aln)
    mafft_tree  = trees.filter { it[0] == 'mafft'  }.map { it[1] }
    muscle_tree = trees.filter { it[0] == 'muscle' }.map { it[1] }
    // join
    OBSERVE_RF(mafft_tree, muscle_tree)
}
