---
name: read-count-lineage-reconciliation
description: Reconcile read counts across pipeline stages and identify unexplained stage breaks.
assay: [all]
target_class: [all]
species_scope: [all]
required_evidence: [raw_reads, clean_reads, spikein_alignment, host_alignment_input, mapped_reads, nuclear_usable_reads, peak_denominator]
contraindications: []
---
# Read Count Lineage Reconciliation

## Decision procedure
Build a per-sample stage ledger. Preserve every observed count and source. Compare adjacent stages only when both values exist. Label a large unexplained drop as a stage break, not missing data. Do not assume that spike-in mapped reads explain the host-alignment input unless project workflow evidence proves that subtraction or filtering rule.
