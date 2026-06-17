---
name: spikein-normalization-review
description: Review spike-in alignment evidence separately from normalization factors.
assay: [cuttag, cutrun, chipseq]
target_class: [all]
species_scope: [all]
required_evidence: [spikein_alignment_input, spikein_mapped_reads, spikein_unique_rate, scaling_factor_formula]
contraindications: [atacseq, scatacseq, amplicon]
---
# Spike-in Normalization Review

## Decision procedure
Report spike-in mapped counts and unique mapping rate as alignment observations. Independently determine whether a scaling factor, its formula, and its application to downstream signal tracks are present. Never infer a scaling factor from unique mapping rate alone. If alignment exists but the factor is absent, state both statuses explicitly.
