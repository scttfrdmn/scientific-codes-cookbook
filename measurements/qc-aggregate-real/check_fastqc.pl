#!/usr/bin/env perl
# Check FastQC's Basic Statistics on every cohort file against an independent recount and a
# published read count, and FastQC's own per-read histograms against its own read total.
#   check_fastqc.pl cohort.tsv recount.tsv <dir holding <sample>.fastqc_data.txt>
# Writes <dir>/fastqc_values.tsv (sample, reads, length, %GC, dedup%, total bases) for task 2.
# Exits non-zero on any failed identity. Every check is exact; nothing here is a band.
use strict; use warnings;
my ($cohort, $recount, $dir) = @ARGV;
die "usage\n" unless $dir;

# FastQC 0.12.1 BasicStats.formatLength, transcribed: scale to the unit, then keep the digits up
# to the first decimal place, dropping it when it is '0'.
sub fmtlen {
    my $x = shift; my $u = " bp";
    if ($x >= 1e9) { $x /= 1e9; $u = " Gbp" } elsif ($x >= 1e6) { $x /= 1e6; $u = " Mbp" }
    elsif ($x >= 1e3) { $x /= 1e3; $u = " kbp" }
    my $s = sprintf("%.15g", $x); $s .= ".0" unless $s =~ /\./;   # Java's ""+double always has a dot
    my ($i, $f) = split /\./, $s;
    return (substr($f, 0, 1) ne '0') ? "$i." . substr($f, 0, 1) . $u : "$i$u";
}

my (%exp, %expb, %rc, @order);
open my $C, '<', $cohort or die; <$C>;
while (<$C>) { chomp; my @f = split /\t/; (my $s = $f[0]) =~ s/\.fastq\.gz$//; $exp{$s} = $f[2]; $expb{$s} = $f[3]; push @order, $s; }
open my $R, '<', $recount or die;
while (<$R>) { chomp; my @f = split /\t/; $rc{$f[0]} = [@f]; }

my $bad = 0; my ($sumq, $sumexp) = (0, 0);
my %reads;
open my $V, '>', "$dir/fastqc_values.tsv" or die;
printf "%-18s %10s %10s %8s %5s %6s %9s  %s\n", qw(sample reads recount length GC dedup bases checks);
for my $s (@order) {
    my $fd = "$dir/$s.fastqc_data.txt";
    open my $F, '<', $fd or do { print "MISSING report $fd\n"; $bad = 1; next };
    my (%bs, $mod, %hist);
    while (<$F>) {
        chomp;
        if (/^>>END_MODULE/) { undef $mod; next }
        if (/^>>(.+?)\t/) { $mod = $1; next }
        next if /^#/ || !defined $mod;
        my @f = split /\t/;
        if ($mod eq 'Basic Statistics') { $bs{$f[0]} = $f[1] }
        elsif ($mod eq 'Sequence Length Distribution' || $mod eq 'Per sequence quality scores') { $hist{$mod} += $f[1] }
    }
    my $dd = '';
    seek $F, 0, 0; while (<$F>) { $dd = (split /\t/)[1] if /^#Total Deduplicated Percentage/ } chomp $dd;
    my $r = $rc{$s} or do { print "MISSING recount for $s\n"; $bad = 1; next };
    my (undef, $n, $bases, $A, $Cc, $G, $T, $N, $oth, $min, $max, $mal) = @$r;
    my $len = ($min == $max) ? "$min" : "$min-$max";
    my $gc = int(100 * ($G + $Cc) / ($A + $Cc + $G + $T));
    my @fail;
    push @fail, "Total Sequences $bs{'Total Sequences'} != recount $n" if $bs{'Total Sequences'} != $n;
    push @fail, "recount $n != published $exp{$s}" if $n != $exp{$s};
    push @fail, "recount bases $bases != published $expb{$s}" if $bases != $expb{$s};
    push @fail, "flagged poor quality $bs{'Sequences flagged as poor quality'}" if $bs{'Sequences flagged as poor quality'} != 0;
    push @fail, "length '$bs{'Sequence length'}' != recount '$len'" if $bs{'Sequence length'} ne $len;
    push @fail, "%GC $bs{'%GC'} != floor(100(G+C)/ACGT) $gc" if $bs{'%GC'} != $gc;
    push @fail, "Total Bases '$bs{'Total Bases'}' != formatLength(recount) '" . fmtlen($bases) . "'" if $bs{'Total Bases'} ne fmtlen($bases);
    for my $m ('Sequence Length Distribution', 'Per sequence quality scores') {
        push @fail, "sum($m) $hist{$m} != Total Sequences" if ($hist{$m} // -1) != $n;
    }
    push @fail, "$mal malformed records" if $mal != 0;
    printf "%-18s %10s %10s %8s %5s %6s %9s  %s\n", $s, $bs{'Total Sequences'}, $n, $bs{'Sequence length'},
        $bs{'%GC'}, sprintf("%.2f", $dd), $bs{'Total Bases'}, @fail ? "FAIL: " . join("; ", @fail) : "exact (10 of 10)";
    $bad = 1 if @fail;
    print $V join("\t", $s, $bs{'Total Sequences'}, $bs{'Sequence length'}, $bs{'%GC'}, $dd, $bases), "\n";
    $reads{$s} = $n; $sumq += $bs{'Total Sequences'}; $sumexp += $exp{$s};
}
close $V;
# mates of a pair must hold the same number of reads
for my $s (grep { /_1(\.filt)?$/ } @order) {
    (my $mate = $s) =~ s{_1(\.filt)?$}{"_2" . ($1 // "")}e;
    if (!defined $reads{$mate} || $reads{$mate} != $reads{$s}) { print "MATES differ: $s vs $mate\n"; $bad = 1 }
    else { print "mates_equal\t$s == $mate == $reads{$s}\n" }
}
my $nrep = scalar(grep { -f "$dir/$_.fastqc_data.txt" } @order);
print "reports\t$nrep of " . scalar(@order) . "\n";
$bad = 1 if $nrep != @order;
print "cohort_reads\tFastQC $sumq == published $sumexp\n";
$bad = 1 if $sumq != $sumexp;
print "checks per file: Total Sequences == recount == published; bases == published; flagged == 0; length range; %GC == floor;\n";
print "  Total Bases == formatLength(recount bases); sum(length dist) == sum(per-seq quality) == N; 0 malformed\n";
exit $bad;
