"""Order- and orientation-free fingerprints of a contig FASTA.

    python3 -u canon.py <contigs.fa>

MEGAHIT names contigs and writes them in an order set by its worker threads, so two assemblies of
the same contig set can differ byte for byte. These fingerprints ignore that: each sequence is
reduced to the lesser of itself and its reverse complement, the set is sorted, and the result is
hashed. Also the multiset of lengths, which needs neither orientation nor order.
"""
import hashlib
import sys

COMP = bytes.maketrans(b"ACGTNacgtn", b"TGCANtgcan")
seqs, cur = [], []
with open(sys.argv[1], "rb") as fh:
    for line in fh:
        if line.startswith(b">"):
            if cur:
                seqs.append(b"".join(cur))
            cur = []
        else:
            cur.append(line.strip())
if cur:
    seqs.append(b"".join(cur))
canon = sorted(min(s, s.translate(COMP)[::-1]) for s in seqs)
print("contigs %d" % len(seqs))
print("contig_set_sha256 %s" % hashlib.sha256(b"\n".join(canon)).hexdigest())
print("length_multiset_sha256 %s" % hashlib.sha256(" ".join(str(n) for n in sorted(len(s) for s in seqs)).encode()).hexdigest())
