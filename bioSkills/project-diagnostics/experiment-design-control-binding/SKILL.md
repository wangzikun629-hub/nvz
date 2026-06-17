---
name: experiment-design-control-binding
description: Resolve condition, replicate, target, control_for, and batch as one authoritative design.
assay: [cuttag, cutrun, chipseq, atacseq, rnaseq]
target_class: [all]
species_scope: [all]
required_evidence: [samplelist, condition, replicate, target, control_for, batch]
contraindications: [amplicon]
---
# Experiment Design Control Binding

## Decision procedure
Use explicit samplelist design fields before name inference. Bind controls through control_for and keep one authoritative relation. Samples with different conditions are not biological replicates. Refuse differential analysis when replicated comparison groups are not established.
