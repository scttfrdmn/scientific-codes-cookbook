#!/usr/bin/env perl
# An independent recount of a FASTQ stream, for checking FastQC's Basic Statistics against.
#   gzip -dc x.fastq.gz | perl recount.pl <name>
# Prints one tab-separated row: name reads bases A C G T N other minlen maxlen malformed.
# FastQC upper-cases each sequence before counting (FastQFile.java), so this does too.
use strict; use warnings;
my $name = shift // die "usage: recount.pl <name>\n";
my ($n, $bases, $a, $c, $g, $t, $nn, $max, $bad) = (0) x 9;
my $min = -1;
while (my $h = <STDIN>) {
    my $s = <STDIN>; my $p = <STDIN>; my $q = <STDIN>;
    if (!defined $q) { $bad++; last; }
    chomp $s; chomp $q;
    $bad++ if substr($h, 0, 1) ne '@' || substr($p, 0, 1) ne '+' || length($s) != length($q);
    $s =~ tr/a-z/A-Z/;
    my $l = length $s;
    $n++; $bases += $l;
    $min = $l if $min < 0 || $l < $min;
    $max = $l if $l > $max;
    $a += ($s =~ tr/A//); $c += ($s =~ tr/C//); $g += ($s =~ tr/G//);
    $t += ($s =~ tr/T//); $nn += ($s =~ tr/N//);
}
my $other = $bases - $a - $c - $g - $t - $nn;
print join("\t", $name, $n, $bases, $a, $c, $g, $t, $nn, $other, $min, $max, $bad), "\n";
