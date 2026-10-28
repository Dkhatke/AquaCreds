# Model card: photo_verifier_v1 (Model C, field-photo verifier)

> Status: DRAFT. The selection rule below was committed before any candidate was evaluated.

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
