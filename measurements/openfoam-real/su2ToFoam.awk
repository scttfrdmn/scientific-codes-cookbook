# su2ToFoam.awk -- a 2D all-quad SU2 mesh to an OpenFOAM polyMesh one cell thick (z = 0..1).
#
#   awk -v out=constant/polyMesh -f su2ToFoam.awk n0012_897-257.su2
#
# OpenFOAM has no SU2 reader. The points are written as SU2's own coordinate text, unchanged, so
# OpenFOAM and SU2 solve on the same vertices. Each quad becomes a hex; each SU2 edge an extruded
# face; each marker a patch (airfoil -> wall, everything else -> patch); front and back are one
# `empty` patch. Internal faces are emitted unsorted (owner, neighbour, nodes) to
# out/internal.unsorted, which the caller sorts into upper-triangular order and finishes with
# su2ToFoamFinish.awk. Fails loudly on anything that is not a 2D quad mesh with every boundary
# edge in a marker.
function die(m) { print "su2ToFoam: " m > "/dev/stderr"; bad = 1; exit 1 }
BEGIN { sec = ""; ne = 0; np = 0; nm = 0 }
/^NDIME=/ { split($0, a, "="); if (a[2] + 0 != 2) die("NDIME " a[2] " != 2"); next }
/^NELEM=/ { split($0, a, "="); NE = a[2] + 0; sec = "e"; next }
/^NPOIN=/ { split($0, a, "="); NP = a[2] + 0; sec = "p"; next }
/^NMARK=/ { split($0, a, "="); NM = a[2] + 0; sec = ""; next }
/^MARKER_TAG=/ { sub(/^MARKER_TAG= */, ""); tag = $0; mk[++nm] = tag; next }
/^MARKER_ELEMS=/ { split($0, a, "="); mcount[tag] = a[2] + 0; sec = "m"; next }
sec == "e" && NF >= 5 {
  if ($1 != 9) die("element type " $1 " is not a quad (9)")
  c0[ne] = $2; c1[ne] = $3; c2[ne] = $4; c3[ne] = $5; ne++; next
}
sec == "p" && NF >= 2 { px[np] = $1; py[np] = $2; np++; next }
sec == "m" && NF >= 3 {
  if ($1 != 3) die("marker element type " $1 " is not a line (3)")
  k = ($2 < $3) ? $2 "," $3 : $3 "," $2
  if (k in mtag) die("edge " k " is in two markers")
  mtag[k] = tag; next
}
END {
  if (bad) exit 1
  if (ne != NE) die("read " ne " elements, header says " NE)
  if (np != NP) die("read " np " points, header says " NP)
  if (nm != NM) die("read " nm " markers, header says " NM)
  # points: z = 0 then z = 1, SU2's coordinate text verbatim
  f = out "/points"
  hdr(f, "vectorField", "points")
  print NP * 2 > f; print "(" > f
  for (i = 0; i < NP; i++) print "(" px[i] " " py[i] " 0)" > f
  for (i = 0; i < NP; i++) print "(" px[i] " " py[i] " 1)" > f
  print ")" > f; close(f)
  flipped = 0; nint = 0
  u = out "/internal.unsorted"
  for (c = 0; c < NE; c++) {
    n[0] = c0[c]; n[1] = c1[c]; n[2] = c2[c]; n[3] = c3[c]
    area = 0
    for (j = 0; j < 4; j++) { p = n[j]; q = n[(j + 1) % 4]; area += px[p] * py[q] - px[q] * py[p] }
    if (area == 0) die("cell " c " has zero area")
    if (area < 0) { t = n[1]; n[1] = n[3]; n[3] = t; flipped++ }   # make every cell counter-clockwise
    cn[c] = n[0] " " n[1] " " n[2] " " n[3]
    for (j = 0; j < 4; j++) {
      p = n[j]; q = n[(j + 1) % 4]
      k = (p < q) ? p "," q : q "," p
      if (k in own) {
        # second cell on this edge: an internal face, oriented as the owner (lower index) saw it
        print own[k], c, ori[k] > u; nint++; delete own[k]; delete ori[k]
      } else { own[k] = c; ori[k] = p " " q " " (q + NP) " " (p + NP) }
    }
  }
  close(u)
  # every remaining edge is a boundary edge and must be in exactly one marker; collected in cell
  # order, so the face order does not depend on awk's hash order
  for (c = 0; c < NE; c++) {
    split(cn[c], m, " ")
    for (j = 1; j <= 4; j++) {
      p = m[j]; q = m[j % 4 + 1]
      k = (p < q) ? p "," q : q "," p
      if (!(k in own)) continue
      if (!(k in mtag)) die("boundary edge " k " is in no marker")
      t = mtag[k]; bc[t]++; bo[t, bc[t]] = c; bf[t, bc[t]] = ori[k]; nb++; isb[k] = 1; delete own[k]
    }
  }
  for (k in mtag) if (!(k in isb)) die("marker edge " k " is not a boundary edge")
  for (i = 1; i <= nm; i++) if (bc[mk[i]] != mcount[mk[i]]) die("marker " mk[i] ": " bc[mk[i]] " boundary edges, header says " mcount[mk[i]])
  # boundary faces and owners, patch by patch, then front/back (empty)
  bfile = out "/boundary.faces"; ofile = out "/boundary.owner"
  start = nint
  pf = out "/boundary"
  hdr(pf, "polyBoundaryMesh", "boundary")
  print nm + 1 > pf; print "(" > pf
  for (i = 1; i <= nm; i++) {
    t = mk[i]
    for (j = 1; j <= bc[t]; j++) { print "4(" bf[t, j] ")" > bfile; print bo[t, j] > ofile }
    print "    " t "\n    {\n        type " ((t == "airfoil") ? "wall" : "patch") ";\n        nFaces " bc[t] ";\n        startFace " start ";\n    }" > pf
    start += bc[t]
  }
  for (c = 0; c < NE; c++) {
    split(cn[c], m, " ")
    print "4(" m[1] " " m[4] " " m[3] " " m[2] ")" > bfile; print c > ofile          # z = 0, normal -z
    print "4(" m[1] + NP " " m[2] + NP " " m[3] + NP " " m[4] + NP ")" > bfile; print c > ofile   # z = 1, normal +z
  }
  print "    frontAndBack\n    {\n        type empty;\n        nFaces " 2 * NE ";\n        startFace " start ";\n    }" > pf
  print ")" > pf; close(pf); close(bfile); close(ofile)
  printf "su2ToFoam: %d points, %d cells (%d reoriented), %d internal faces, %d boundary edges", NP * 2, NE, flipped, nint, nb
  for (i = 1; i <= nm; i++) printf ", %s %d", mk[i], bc[mk[i]]
  printf "\n"
  print NP * 2, NE, nint, nint + nb + 2 * NE > (out "/counts")
}
function hdr(file, cls, obj) {
  print "FoamFile\n{\n    version 2.0;\n    format ascii;\n    class " cls ";\n    location \"constant/polyMesh\";\n    object " obj ";\n}" > file
}
