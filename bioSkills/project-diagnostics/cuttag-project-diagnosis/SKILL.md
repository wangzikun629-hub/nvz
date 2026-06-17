---
name: cuttag-project-diagnosis
description: Evidence-chain diagnosis for CUT&Tag project quality and interpretation.
assay: [cuttag]
target_class: [histone_mark, transcription_factor, control]
species_scope: [all]
required_evidence: [ReadsQC, AlignmentQC, library_complexity, peak, FRiP, correlation, experiment_design]
contraindications: [atacseq, scatacseq, amplicon]
---
# CUT&Tag Project Diagnosis

## Decision procedure
Confirm assay and sample roles. Reconcile reads from raw and clean stages through host alignment and the FRiP denominator. Evaluate sequencing depth, mapping, organelle fraction, library complexity, peak evidence, FRiP, and replicate-stratified correlation as one chain. Treat controls and target classes separately. Stop at limitations when biological replicates, grouping, or normalization evidence is absent.
