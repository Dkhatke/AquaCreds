# Model card: photo_verifier_v1 (Model C, field-photo verifier)

**Shipped candidate:** `probe_bioclip_v1`, a multinomial logistic-regression head on the frozen
BioCLIP ViT-B/16 image encoder, exported as one ONNX graph (`model.onnx`, fp32, 345 MB).
**Status:** evaluated; served only behind `PHOTO_VERIFIER_ENABLED=false` (default) and only as an
advisory check. It is **not yet validated on photos from the AquaCreds app**.

**Task:** given a field photo, estimate P(the photo shows mangrove vegetation) and, if it does, rank
the 11 Indian mangrove genera. This is one of the six evidence checks in the trust score. It never
rejects a submission by itself.

Numbers come from `ml/model_cards/photo_verifier_v1_metrics.json`, written by
`ml/pipelines/photo_eval.py evaluate` and `ml/pipelines/photo_export.py probe_bioclip_v1`.

## Selection rule (fixed before running the evaluation)

Candidates: (a) BioCLIP-family zero-shot, (b) linear probe on BioCLIP-family embeddings,
(c) MobileNetV2 fine-tuned from ImageNet weights. All use the same splits
(`ml/pipelines/photos_splits.py`).

1. **Primary metric:** binary F1 (mangrove vs not) on `test_observer`, at the decision threshold
   that maximises F1 on `val` (the threshold is never tuned on a test set).
2. **Eligibility:** the candidate must export to ONNX with a passing parity check, have an
   estimated Oracle ARM 4-core latency under 3 s per photo, and have weights whose licence permits
   use in this project.
3. **Winner:** the eligible candidate with the highest primary F1. If the 95% observer-cluster
   bootstrap CI of the paired F1 difference between the top two includes 0, the one with the lower
   measured CPU latency wins (cheaper to serve, same evidence of quality).
4. `test_state` (held-out Maharashtra) metrics are reported, never used for selection. If the
   winner's `test_state` AUROC is below 0.80, this card says it is not fit to be enabled.

**Applying the rule:** `probe_bioclip_v1` has the highest `test_observer` F1 (0.988). The paired
observer-bootstrap F1 difference against the runner-up `probe_bioclip25_student` is +0.046,
95% CI [0.017, 0.079]. That CI excludes 0, so the latency tie-break does not apply. Eligibility:
ONNX parity passes (max |ΔP(mangrove)| = 1.6e-6 over 200 test images, tolerance 1e-4); estimated
ARM latency is about 0.1 s, far below the 3 s limit; the weights are MIT-licensed. Its
`test_state` AUROC is 0.996, above the 0.80 bar.

## Data

`ml/pipelines/photos_download.py` → `data/photos_manifest.csv` (committed; one row per image with
licence, iNaturalist attribution string, observer, observation URL, coordinates, Indian state,
SHA-256 of the stored file).

* Source: iNaturalist API, research-grade observations only. Only photos whose own licence is
  CC0, CC BY or CC BY-NC are kept. One photo per observation, at most 30 per observer per class.
  Requests were ≤ 1/s with a descriptive User-Agent. Images were resized to ≤ 512 px and re-encoded,
  which strips EXIF.
* 2,865 rows listed and **2,861 images stored** (4 timed out twice). Licences: CC BY-NC 2,189 ·
  CC BY 546 · CC0 126. Regions: India 1,624 · rest of Asia 1,191 · rest of world 46 (Ceriops only).
  1,115 observers.
* **Positives (1,704):** the 11 genera in `data/reference/wood_density_mangroves.csv`. Each genus
  has 155 images (Heritiera 154), India first, then Asia. Indian counts are small for Ceriops (15),
  Xylocarpus (14), Heritiera (20) and Lumnitzera (22).
* **Hard negatives (1,157):** Prosopis/Neltuma juliflora 160, coconut 150, Casuarina
  equisetifolia 150, salt marsh (Suaeda, Salicornia, Sesuvium) 159, beach/dune (Ipomoea pes-caprae,
  Spinifex littoreus) 110, coastal trees (Thespesia populnea, Pandanus) 109, grassland (Saccharum
  spontaneum) 109, plus two **habitat proxies**:
  * `mudflat_proxy` 110: shorebirds (Calidris) and mudskippers (Boleophthalmus) photographed on
    open mud;
  * `urban_proxy` 100: house-crow observations, mostly taken in towns.

  These stand in for scene types that iNaturalist does not index. **Aquaculture ponds have no
  iNaturalist proxy and are not covered.** Indoor scenes and screen recaptures are not covered
  either (the capture nonce and EXIF checks address those).
* **Splits** (`ml/pipelines/photos_splits.py`, `data/photos_splits.csv`): every Indian photo from
  **Maharashtra** goes to `test_state` (354 images, 112 observers). Observers who also have photos
  elsewhere lose those other photos (69 images, `excluded_overlap`). Everything else is split by
  a salted hash of the observer id: train 1,568 / val 472 / `test_observer` 398. No observer
  spans two splits (this is checked). Val happens to be 76% mangrove (one prolific observer); the
  hash split was pre-specified, so it was not re-drawn.

## Candidates

The official checkpoints were **not downloaded**, because each single file exceeds the
user's 500 MB limit:
`imageomics/bioclip-2` (`open_clip_model.safetensors` 1,710,517,724 bytes) and
`imageomics/bioclip` ViT-B/16 (`open_clip_pytorch_model.bin` 598,599,013 bytes). Evaluated instead:

| Candidate | Weights (download) | What it is |
|---|---|---|
| `zeroshot_bioclip25_student` | `crazedcodernate/bioclip-2.5-mobile-fastvit` fp32 ONNX 46,986,589 B + `taxa_table.npy` 17,494,144 B (MIT) | A third-party FastViT student distilled from BioCLIP 2.5. Zero-shot over its fixed 4,271-species iNat21 plant text-embedding table; P(mangrove) is the softmax mass on the mangrove taxa in the table (Avicennia ×2, Rhizophora mangle, Laguncularia, Conocarpus). There is no text encoder, so **no custom genus or negative prompts are possible**, and 9 of the 11 Indian genera are absent from the table. |
| `probe_bioclip25_student` | same | Logistic regression on the student's 1024-d embeddings |
| `probe_bioclip_v1` | `birder-project/vit_b16_pn_bioclip-v1` 344,841,215 B (MIT) | The original BioCLIP ViT-B/16 **image encoder** (with its 512-d projection) in Birder format. It is loaded into open_clip's ViT-B-16 with a 1:1 key mapping (`strict=True`; the projection bias is checked to be zero). Logistic regression on its embeddings. This is not an official Imageomics upload; the conversion claims to preserve the weights. |
| `mobilenetv2_finetune` | torchvision `mobilenet_v2-7ebf99e0.pth` (IMAGENET1K_V2) 13.6 MB | Partial fine-tune, CPU: feature blocks 0-13 frozen (BatchNorm statistics fixed), blocks 14-18 + classifier trained, 10 epochs, AdamW 5e-4, label smoothing 0.1, best epoch by val log-loss |

All supervised candidates predict the 20 dataset classes. P(mangrove) is the sum over the genus
classes; the genus ranking uses the genus-class probabilities. The probe's C ∈ {0.1 … 30} was
chosen by val log-loss. Input for every candidate, and in production: RGB, plain bilinear resize
to 224×224, normalisation folded into the model.

**MobileNetV2 resource note:** the first run fine-tuned the whole network on MPS. Its process
footprint reached 5.4 GB, over the 3 GB limit. (That run scored observer F1 0.931 and state F1 0.923;
it is kept for the record in `ml/artifacts`.) With a 1.5 GB MPS allocator cap the run ran out of
memory (process footprint 2.95 GB at the OOM). Full CPU fine-tuning ran at 312 s/epoch. The
reported run is therefore the CPU partial fine-tune above, with a 1.68 GB peak footprint.
A full fine-tune, MobileNetV4 or EfficientNetV2 might do better; they were not tried.

## Results (95% CIs: 1,000 observer-cluster bootstrap resamples)

Thresholds are chosen on val: **F1/precision/recall** use the val best-F1 threshold, and
**FAR @ val-R95** is the false-accept rate (non-mangrove scored as mangrove) at the threshold that
gives 95% recall on val. ECE uses 10 bins on P(mangrove). Genus top-k counts mangrove images
only, over the 11 genera.

**test_observer** (398 images, 198 mangrove, 185 observers)

| Candidate | Precision | Recall | F1 | AUROC | FAR @ val-R95 | ECE | Genus top-1 | Genus top-3 |
|---|---|---|---|---|---|---|---|---|
| `zeroshot_bioclip25_student` | 0.754 [0.665, 0.843] | 0.884 [0.844, 0.922] | 0.814 [0.754, 0.864] | 0.899 [0.857, 0.932] | 0.415 [0.331, 0.519] | 0.413 [0.312, 0.535] | 0.111 [0.061, 0.170] | 0.227 [0.152, 0.316] |
| `probe_bioclip25_student` | 0.903 [0.839, 0.959] | 0.985 [0.967, 1.000] | 0.942 [0.905, 0.971] | 0.992 [0.986, 0.997] | 0.035 [0.006, 0.070] | 0.031 [0.025, 0.052] | 0.717 [0.642, 0.782] | 0.914 [0.873, 0.951] |
| `probe_bioclip_v1` | 0.975 [0.952, 0.995] | 1.000 [1.000, 1.000] | 0.988 [0.975, 0.997] | 0.997 [0.989, 1.000] | 0.005 [0.000, 0.021] | 0.026 [0.019, 0.035] | 0.798 [0.730, 0.854] | 0.950 [0.910, 0.982] |
| `mobilenetv2_finetune` | 0.878 [0.794, 0.954] | 0.985 [0.965, 1.000] | 0.929 [0.880, 0.970] | 0.989 [0.983, 0.997] | 0.050 [0.018, 0.074] | 0.082 [0.070, 0.106] | 0.566 [0.490, 0.646] | 0.849 [0.793, 0.893] |

**test_state** (354 images, 228 mangrove, 112 observers)

| Candidate | Precision | Recall | F1 | AUROC | FAR @ val-R95 | ECE | Genus top-1 | Genus top-3 |
|---|---|---|---|---|---|---|---|---|
| `zeroshot_bioclip25_student` | 0.825 [0.726, 0.889] | 0.930 [0.890, 0.968] | 0.874 [0.809, 0.914] | 0.895 [0.850, 0.936] | 0.524 [0.420, 0.636] | 0.522 [0.424, 0.587] | 0.241 [0.182, 0.326] | 0.373 [0.299, 0.457] |
| `probe_bioclip25_student` | 0.894 [0.829, 0.941] | 1.000 [1.000, 1.000] | 0.944 [0.906, 0.969] | 0.996 [0.993, 0.999] | 0.087 [0.037, 0.141] | 0.036 [0.029, 0.068] | 0.618 [0.571, 0.671] | 0.855 [0.821, 0.894] |
| `probe_bioclip_v1` | 0.927 [0.880, 0.960] | 1.000 [1.000, 1.000] | 0.962 [0.936, 0.980] | 0.996 [0.992, 0.999] | 0.032 [0.007, 0.064] | 0.023 [0.025, 0.050] | 0.689 [0.632, 0.759] | 0.877 [0.834, 0.919] |
| `mobilenetv2_finetune` | 0.861 [0.793, 0.922] | 0.978 [0.954, 0.996] | 0.916 [0.875, 0.950] | 0.963 [0.932, 0.990] | 0.222 [0.107, 0.322] | 0.076 [0.067, 0.113] | 0.539 [0.467, 0.615] | 0.776 [0.718, 0.833] |

**test_observer_observed_2021plus** (181 images, 101 mangrove, 86 observers)

| Candidate | Precision | Recall | F1 | AUROC | FAR @ val-R95 | ECE | Genus top-1 | Genus top-3 |
|---|---|---|---|---|---|---|---|---|
| `zeroshot_bioclip25_student` | 0.896 [0.815, 0.971] | 0.852 [0.779, 0.906] | 0.873 [0.817, 0.920] | 0.935 [0.897, 0.966] | 0.237 [0.111, 0.339] | 0.487 [0.323, 0.685] | 0.129 [0.049, 0.218] | 0.208 [0.100, 0.337] |
| `probe_bioclip25_student` | 0.952 [0.892, 1.000] | 0.970 [0.931, 1.000] | 0.961 [0.925, 0.990] | 0.994 [0.988, 0.999] | 0.025 [0.000, 0.045] | 0.041 [0.026, 0.079] | 0.772 [0.686, 0.861] | 0.941 [0.892, 0.989] |
| `probe_bioclip_v1` | 0.971 [0.931, 1.000] | 1.000 [1.000, 1.000] | 0.985 [0.964, 1.000] | 1.000 [0.999, 1.000] | 0.000 [0.000, 0.000] | 0.030 [0.022, 0.044] | 0.832 [0.736, 0.909] | 0.980 [0.953, 1.000] |
| `mobilenetv2_finetune` | 0.877 [0.768, 0.964] | 0.990 [0.966, 1.000] | 0.930 [0.864, 0.977] | 0.983 [0.973, 0.997] | 0.062 [0.018, 0.111] | 0.078 [0.066, 0.137] | 0.525 [0.417, 0.630] | 0.822 [0.755, 0.887] |

**test_state_observed_2021plus** (162 images, 140 mangrove, 68 observers)

| Candidate | Precision | Recall | F1 | AUROC | FAR @ val-R95 | ECE | Genus top-1 | Genus top-3 |
|---|---|---|---|---|---|---|---|---|
| `zeroshot_bioclip25_student` | 0.963 [0.924, 0.993] | 0.936 [0.879, 0.982] | 0.949 [0.914, 0.977] | 0.936 [0.869, 0.983] | 0.591 [0.333, 0.818] | 0.695 [0.613, 0.758] | 0.314 [0.232, 0.382] | 0.414 [0.318, 0.491] |
| `probe_bioclip25_student` | 0.959 [0.904, 0.994] | 1.000 [1.000, 1.000] | 0.979 [0.949, 0.997] | 0.992 [0.981, 0.999] | 0.182 [0.000, 0.400] | 0.037 [0.026, 0.068] | 0.614 [0.544, 0.684] | 0.864 [0.808, 0.915] |
| `probe_bioclip_v1` | 0.952 [0.906, 0.985] | 1.000 [1.000, 1.000] | 0.976 [0.951, 0.993] | 0.987 [0.971, 0.998] | 0.091 [0.000, 0.222] | 0.033 [0.031, 0.072] | 0.679 [0.592, 0.772] | 0.886 [0.821, 0.939] |
| `mobilenetv2_finetune` | 0.952 [0.903, 0.985] | 0.993 [0.975, 1.000] | 0.972 [0.945, 0.990] | 0.984 [0.965, 0.997] | 0.182 [0.000, 0.381] | 0.131 [0.114, 0.169] | 0.543 [0.438, 0.633] | 0.771 [0.683, 0.853] |

Notes:

* **Pretraining overlap:** BioCLIP (TreeOfLife-10M) and the student (iNat21) include iNaturalist
  photos up to 2020, so some pre-2021 test images may have been seen in pretraining. The
  `*_observed_2021plus` rows use only images observed from 2021 on. The probes are not worse there
  (observer F1 0.985 vs 0.988), which is no evidence of inflation. It cannot rule out
  same-observer or same-plant overlap.
* The zero-shot candidate's val-optimal threshold is ≈ 0 (it labels almost everything mangrove,
  and val is 76% mangrove), so its F1 mostly reflects the class mix; its AUROC (0.90) is the
  informative number. Its genus scores are poor because 9 genera are missing from its table.
* Recall CIs of [1.000, 1.000] mean no test mangrove photo fell below the val threshold. They do
  not mean recall is perfect on field photos.

**Where `probe_bioclip_v1` fails (non-mangrove photos with P(mangrove) ≥ 0.189):**
`test_observer`: beach/dune 2/12, coconut 1/34, grassland 1/25, Prosopis 1/45, every other class 0.
`test_state` (Maharashtra): coastal trees 6/17, Casuarina 4/13, beach/dune 4/20, mudflat proxy
2/17, coconut 1/22, urban proxy 1/26. The look-alikes that come up again and again are
Thespesia/Pandanus, Casuarina and Ipomoea pes-caprae.

## Serving

* `ml/artifacts/photo_verifier/export/model.onnx`: SHA-256
  `3609c78f1ff91dbfd83a84dc2c58d6006c13497f99ea303683e4269b17971e69`, 345,207,351 bytes, opset
  18, dynamic batch. Input `image` [N,3,224,224] RGB 0..1; output `probabilities` [N,20].
* `labels.json`: SHA-256 `1f54be790ddfe83a5d9469ebb15734194385507e9eaf874909fcc944edc19ae1`
  (classes, input contract, thresholds).
* Both hashes are pinned in `aqua_worker.photo_verifier.PHOTO_VERIFIER_V1`. The worker refuses
  any other bytes. Publish with `python -m aqua_worker.photo_verifier publish
  ml/artifacts/photo_verifier/export`. The files are git-ignored and live only in the local
  clone (no DVC remote yet), the same arrangement as the ecosystem classifier.
* **Latency** (ONNX Runtime CPU, batch 1, including JPEG decode and resize), measured on an Apple M5
  Mac: median 51 ms (1 thread), 43 ms (2 threads, the worker setting), 43 ms (4 threads).
  **Oracle ARM 4-core: ESTIMATE 87-130 ms per photo, not measured.** The estimate assumes
  Ampere A1 cores are 2-3× slower than an M-series core. Measure on the VM before relying on it.
* Memory: the export process peaked at a 2.3 GB footprint. Worker inference holds the ~345 MB
  model plus one image.

## Trust-score proposal (implemented, off by default)

`aqua_worker.evidence.check_photo_model`, with bands fixed on **val only** and stored in
`labels.json`:

| P(mangrove) | Outcome | Penalty | Meaning |
|---|---|---|---|
| ≥ 0.704 (`pass_at` = val recall-95% threshold) | pass | 0 | looks like mangrove; top genus shown |
| 0.189-0.704 (`low` = val best-F1 threshold) | warn | 20 (`WARN_PENALTY`) | uncertain; verifier checks the photo |
| < 0.189 | warn | 20 | does not look like mangrove; verifier checks the photo |

* It is **never `hard`**, so it can never reject a submission on its own. A photo that fails only
  this check scores 80 and goes to the verifier, who decides. The full model output
  (`mangrove_prob`, `top_genera`, `model_version`, model `sha256`, `image_sha256`) is stored in
  the check's `details` for traceability.
* Test-set band rates: `test_observer` mangrove 98.0% pass / 2.0% uncertain; non-mangrove 97.5%
  unlikely / 2.0% uncertain / 0.5% pass. `test_state` mangrove 94.7% pass / 5.3% uncertain;
  non-mangrove 85.7% unlikely / 11.1% uncertain / 3.2% pass.
* Proposed weight stays at the standard 20 until the model is validated on real app captures
  (domain shift: iNaturalist close-ups vs. wide field shots of plots, mud, saplings). Revisit then;
  do not raise it on iNaturalist evidence alone.
* If it is enabled but the model cannot be loaded, the check is `skipped` with the error as its
  reason (and an error log). It never produces a made-up result.

## Limitations

* iNaturalist photos are not the deployment distribution. Expect lower accuracy on wide shots,
  saplings, night or rain photos, and plots without visible leaves or fruit.
* Aquaculture ponds, indoor scenes and screens are not in the negative set.
* Some genera have few Indian images. Genus ranking is a hint for the verifier, not an
  identification.
* The weights are a third-party conversion of BioCLIP. Re-run the evaluation if the official
  ViT-B/16 or BioCLIP 2 (needs approval: 599 MB and 1.71 GB single files) is approved.
* The licence mix is 76% CC BY-NC. Model weights trained on NC images are a grey area; the
  per-image attribution is kept in the manifest.

## Reproduce

```
uv sync --all-packages --group ml
uv run python -u ml/pipelines/photos_download.py list && uv run python -u ml/pipelines/photos_download.py fetch
uv run python ml/pipelines/photos_splits.py
uv run python -u ml/pipelines/photo_eval.py embed && uv run python -u ml/pipelines/photo_eval.py evaluate
uv run python -u ml/pipelines/photo_export.py probe_bioclip_v1
```
(weights under `ml/artifacts/hf_downloads/`, caches under `ml/artifacts/cache/`; both git-ignored)
