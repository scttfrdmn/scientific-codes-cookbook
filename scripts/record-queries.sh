#!/bin/sh
# Twelve worked queries over catalog/record.json -- the format is practices/measurement-record.md.
# Run from the repo root: sh scripts/record-queries.sh. `A` keeps the rows that may answer "which box".
R=catalog/record.json
A='def ok: select(.purpose != "diagnostic" and .run_outcome == "completed" and (.superseded_by | not));'
M='.measurements | to_entries[] | .key as $m | .value'
# Q1 cheapest box per simulated ns, within 2x of 80k atoms
jq -r "$A $M"' | .workload.size.atoms as $a | .run[]? | ok | select((.size.atoms // $a // 0) | . >= 40000 and . <= 160000)
  | select(.usd_per_result) | [.usd_per_result, $m, .instance, "\(.ranks)x\(.threads_per_rank)", .throughput] | @tsv' $R | sort -g | head -3
# Q2 does salmon scale past 16 cores? and bowtie2
jq -r '.measurements["salmon-real"].not_swept[]? | "salmon \(.axis): not measured -- \(.reason)"' $R
jq -r '.measurements["bowtie2-real"].series[] | select(.axis == "workers")
  | "bowtie2 verdict \(.verdict | tostring), cost knee \(.curve.cost_knee | tostring)", (.curve.points[] | "  \(.axis) cores eff \(.efficiency) billed $\(.billed_usd)")' $R
# Q3 GPU vs CPU $/result: every accelerator series
jq -r "$M"' | .series[]? | select(.axis == "accelerator") | "\($m) \(.id): attribution \(.attribution)", (.curve.points | sort_by(.compute_usd)[] | "  $\(.compute_usd)/result  \(.axis)  \(.throughput)")' $R
# Q4 bwa: fastest chip, cheapest result
jq -r '.measurements["bwa-real"].series[] | select(.id == "chip") | .curve | "fastest \(.points | min_by(.wall_s) | .axis); cheapest \(.cost_knee.at) on \(.cost_knee.on)"' $R
# Q5 what does one whole-genome bwa alignment cost?
jq -r "$A"' .measurements["bwa-real"] | "measured at \(.workload.size | tostring)",
  ([.run[] | ok | select(.billed_usd)] | min_by(.billed_usd) | "  cheapest $\(.billed_usd) billed on \(.instance)"),
  "  size series: \([.series[] | select(.axis == "size")] | length) -- no extrapolation to 30x"' $R
# Q6 most idle rented capacity (effective $/hr / paid)
jq -r "$A $M"' | .run[]? | ok | select(.effective_usd_hr) | [(.effective_usd_hr / .usd_hr * 100 | round / 100), $m, .instance, .binding, .util[.binding]] | @tsv' $R | sort -gr | head -5
# Q7 where is bwa's knee?
jq -r '.measurements["bwa-real"].series[] | select(.id == "knee") | (.curve.points[] | "  \(.axis) cores speedup \(.speedup) eff \(.efficiency) billed $\(.billed_usd)"), "  derived cost knee \(.curve.cost_knee | tostring); authored \(.verdict | tostring)"' $R
# Q8 Graviton2 -> Graviton5 speedup by wall, every generation series
jq -r "$M"' | .series[]? | select(.axis == "generation") | (.curve.points | map({(.axis | tostring): .wall_s}) | add) as $w
  | select($w["6"] and $w["9"]) | [($w["6"] / $w["9"] * 100 | round / 100), $m, .equal_work] | @tsv' $R | sort -gr
# Q9 how many concurrent jobs fill a box? every concurrent series
jq -r "$M"' | .series[]? | select(.axis == "concurrent") | "\($m) \(.id): verdict \(.verdict // {} | tostring)", (.curve.points[] | "  N=\(.axis) speedup \(.speedup) eff \(.efficiency) $\(.compute_usd)")' $R
# Q10 which configurations rest on more than one run, and their spread
jq -r "$M"' | .run[]? | select(.n > 1) | "\($m) \(.instance) \(.id // "") n=\(.n) wall \(.wall_spread) rate \(.throughput_spread)"' $R | sort -u
# Q11 rows where most of the billed window is not the tool, with the window's basis
jq -r "$A $M"' | .run[]? | ok | select(.billed_s and .wall_s) | [((.billed_s - .wall_s) / .billed_s * 100 | round), $m, .instance, .billed_basis] | @tsv' $R | sort -gr | head -8
# Q12 recorded capacity trouble or failed rungs
jq -r "$M"' | .run[]? | select((.run_outcome // "completed") != "completed") | "\($m) \(.instance) \(.date) \(.run_outcome)"' $R
