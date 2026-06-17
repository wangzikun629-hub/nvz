---
name: frip-peak-correlation-diagnosis
description: Diagnose FRiP with peak, read-depth, role, and stratified correlation evidence.
assay: [cuttag, cutrun, chipseq]
target_class: [histone_mark, transcription_factor, control]
species_scope: [all]
required_evidence: [FRiP_numerator, FRiP_denominator, peak_set, peak_count, alignment, library_complexity, correlation, experiment_design]
contraindications: [atacseq, scatacseq, amplicon]
---
# FRiP Peak Correlation Diagnosis

## Decision procedure
Validate FRiP scale and recompute reads-in-peaks divided by its declared denominator. Separate self-FRiP from cross-FRiP. Interpret correlation only within design strata such as biological replicates, condition, target, controls, and batch. Do not use the global matrix minimum as project quality evidence.
