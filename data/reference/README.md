# Reference tables

Small, cited reference tables committed to git (not DVC-tracked: each is a few kB).

## `wood_density_mangroves.csv`: wood density (ρ) for Indian mangrove species

Used by `aqua_mrv.wood_density.lookup()` to feed ρ into the Komiyama et al. 2005 mangrove
allometry. An identical copy ships inside the package
(`packages/aqua_mrv/src/aqua_mrv/data/`); a test checks the two are byte-identical.

**Source:** Global Wood Density Database (GWDD v1): Zanne, A.E., Lopez-Gonzalez, G., Coomes, D.A.,
Ilic, J., Jansen, S., Lewis, S.L., Miller, R.B., Swenson, N.G., Wiemann, M.C., Chave, J. (2009).
*Data from: Towards a worldwide wood economics spectrum.* Dryad,
[doi:10.5061/dryad.234](https://doi.org/10.5061/dryad.234). Data paper: Chave et al. 2009,
*Ecology Letters* 12:351–366, doi:10.1111/j.1461-0248.2009.01285.x.

**Licence:** CC0 1.0 (public domain dedication), as stated on the Dryad record (checked through the
Dryad API on 2026-10-19). Citation is still good practice and is recorded in every row.

**How it was retrieved (2026-10-19).** Dryad's file endpoint (`/api/v2/files/94836/download`,
`GlobalWoodDensityDatabase.xls`, 2,047,488 bytes) now requires an authenticated API token, and the
browser download is behind a bot challenge. The records were therefore read from the verbatim copy
of the GWDD distributed as the `wdData` dataset in the CRAN package **BIOMASS 2.2.7-1**
(`BIOMASS_2.2.7-1.tar.gz`, md5 `f7cf5d314611a9f47730417ac979f61a`; its documentation cites
Zanne et al. 2009, Dryad, as the source). That copy has 16,467 records with family, genus, species,
wood density (g/cm³), region and GWDD reference number. It was not checksum-compared with the Dryad
`.xls`; re-run `build_wood_density.py` against the original once it can be downloaded.

**Rebuild:** see the docstring of [`build_wood_density.py`](build_wood_density.py).

**Method**
- `rank=species` rows: arithmetic mean of all GWDD records for that binomial, with `n`, SD,
  min, max, the GWDD regions and reference numbers. Records are averaged as published (duplicate
  values from one reference are kept, as in GWDD).
- `rank=genus` rows: mean of every GWDD record in the genus (all species, all regions). These are
  used, and flagged `genus_fallback=true`, only for a species that has no species row
  (e.g. `Avicennia sp.`). Every report that uses a genus fallback carries a warning.
- An unknown genus raises `UnknownSpeciesError`: no value is ever guessed.
- Region: none of the GWDD records for these species is from India except *Sonneratia apetala*,
  *Excoecaria agallocha* and *Heritiera littoralis* (one Indian record each); most are
  South-East Asian or Australian/PNG. This is a known limitation: region-specific or
  site-measured densities should replace these when available.

**Coverage** (all 18 required species have species-level records; no fallback needed):

| Species | ρ (g/cm³) | n | Fallback |
|---|---|---|---|
| *Avicennia marina* | 0.6478 | 4 | no |
| *Avicennia officinalis* | 0.6050 | 2 | no |
| *Avicennia alba* | 0.5867 | 3 | no |
| *Rhizophora mucronata* | 0.8140 | 5 | no |
| *Rhizophora apiculata* | 0.8425 | 2 | no |
| *Bruguiera gymnorhiza* | 0.7638 | 4 | no |
| *Bruguiera cylindrica* | 0.7200 | 1 | no |
| *Ceriops tagal* | 0.8374 | 5 | no |
| *Ceriops decandra* | 0.7700 | 1 | no |
| *Sonneratia apetala* | 0.5285 | 2 | no |
| *Sonneratia alba* | 0.5085 | 2 | no |
| *Sonneratia caseolaris* | 0.3885 | 2 | no |
| *Excoecaria agallocha* | 0.4048 | 6 | no |
| *Heritiera fomes* | 0.8160 | 1 | no |
| *Kandelia candel* | 0.5097 | 3 | no |
| *Aegiceras corniculatum* | 0.5100 | 1 | no |
| *Lumnitzera racemosa* | 0.7100 | 1 | no |
| *Xylocarpus granatum* | 0.5673 | 3 | no |

Also included: *Rhizophora stylosa*, *Bruguiera parviflora*, *B. sexangula*, *Sonneratia
griffithii*, *Heritiera littoralis*, *Lumnitzera littorea*, *Xylocarpus moluccensis*, plus genus
means for all 11 genera.

**Newer data:** GWDD v2 (Fischer et al. 2026, Zenodo doi:10.5281/zenodo.18262736, CC BY 4.0;
109,626 records, with hierarchical species/genus estimates) includes and supersedes v1. Switching to
it is a future change (different licence and method, so values and provenance would change).
