from __future__ import annotations

import ast
import math
import operator
import re
import statistics
import threading
from copy import deepcopy
from typing import Any


class MetricSchemaService:
    """Canonical metric definitions and scale-aware value normalization.

    Phase 0（见 docs/project_analysis_agent_upgrade_plan.md 第 3 节）改造点：
    `METRICS` 保留为人工维护的静态基线（阈值/公式/单位/verifier_contract 完全不变），
    对外查询一律经过 `_registry()`，它是一份运行时可追加条目的可变副本。
    `register_metric()` 是给后续 Phase 1.5"候选指标转正"使用的唯一写入口——
    在候选指标转正前，这里不会被除测试外的任何代码调用，因此现有行为完全不受影响。
    """

    SCHEMA_VERSION = "metric-schema-v1"

    METRICS: dict[str, dict[str, Any]] = {
        "adapter_percent": {
            "label": "Adapter read-through rate",
            "label_zh": "原始 reads 接头检出率",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "adapter_affected_reads / raw_reads * 100",
            "numerator": "reads affected by adapter sequence",
            "denominator": "raw reads examined",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "recompute": "ratio",
            "verifier_contract": "citation_only",
        },
        "clean_read_retention_percent": {
            "label": "Read-pair retention after trimming",
            "label_zh": "过滤后 reads 保留率",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "written_pairs / processed_pairs * 100",
            "numerator": "read pairs written after trimming",
            "denominator": "processed read pairs before trimming output",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "recompute": "ratio",
            "verifier_contract": "display_value_only",
        },
        "frip_ratio": {
            "label": "FRiP",
            "label_zh": "FRiP",
            "unit": "fraction",
            "display_unit": "%",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "reads_in_peaks / usable_mapped_reads_or_fragments",
            "numerator": "reads in called peaks",
            "denominator": "usable mapped reads/fragments evaluated against the peak set",
            "source_scale": "fraction",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "mapping_rate_percent": {
            "label": "Mapping rate",
            "label_zh": "比对率",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "mapped_reads / alignment_input_reads * 100",
            "numerator": "mapped reads",
            "denominator": "alignment input reads",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "unique_mapping_rate_percent": {
            "label": "Unique mapping rate",
            "label_zh": "唯一比对率",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "uniquely_mapped_reads / alignment_input_reads * 100",
            "numerator": "uniquely mapped reads",
            "denominator": "alignment input reads",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "duplicate_rate_percent": {
            "label": "Duplicate rate",
            "label_zh": "重复率",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "duplicate_reads_or_fragments / examined_mapped_reads_or_fragments * 100",
            "numerator": "duplicate reads/fragments",
            "denominator": "examined mapped reads/fragments",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "recompute": "ratio",
            "verifier_contract": "citation_only",
        },
        "picard_duplicate_pair_rate_percent": {
            "label": "Picard duplicate pair rate",
            "label_zh": "Picard read-pair 重复率",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "PERCENT_DUPLICATION * 100",
            "numerator": "duplicate read pairs",
            "denominator": "Picard examined read pairs",
            "source_scale": "fraction",
            "assay_scope": ["all"],
            "recompute": "ratio",
            "verifier_contract": "citation_only",
        },
        "mt_rate_percent": {
            "label": "Organelle alignment rate",
            "label_zh": "线粒体比对 reads 比例",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "organelle_mapped_reads / mapped_reads * 100",
            "numerator": "organelle mapped reads",
            "denominator": "mapped reads",
            "source_scale": "percent",
            "assay_scope": ["all"],
            # 2026-07-02 止血：此前未声明 verifier_contract，被 verifier_contract() 的
            # 默认档静默当作 strict_formula_recalculation 对待（见方案 §1.3）。现改为
            # 显式声明，行为不变（仍是 a/b 比值，输入齐时严格重算），但不再依赖默认陷阱。
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "q20_ratio": {
            "label": "Q20",
            "label_zh": "Q20 碱基比例",
            "unit": "fraction",
            "display_unit": "%",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "bases_with_quality_ge_20 / total_bases",
            "numerator": "bases with Q >= 20",
            "denominator": "bases in the reported read population",
            "source_scale": "percent",
            "assay_scope": ["all"],
            # 2026-07-02 止血：同 mt_rate_percent，补齐显式声明，消灭默认 strict 陷阱。
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "q30_ratio": {
            "label": "Q30",
            "label_zh": "Q30 碱基比例",
            "unit": "fraction",
            "display_unit": "%",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "bases_with_quality_ge_30 / total_bases",
            "numerator": "bases with Q >= 30",
            "denominator": "bases in the reported read population",
            "source_scale": "percent",
            "assay_scope": ["all"],
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "nrf": {
            "label": "NRF",
            "label_zh": "NRF 文库复杂度",
            "unit": "fraction",
            "display_unit": "",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "distinct_genomic_locations / total_mapped_fragments",
            "numerator": "distinct genomic locations",
            "denominator": "total mapped fragments",
            "source_scale": "fraction",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
            # 2026-07-02 止血：NRF 需要位点分布重算，汇总表通常只有最终比值，分子分母
            # 拿不到。此前落入默认 strict 陷阱，静默盖章。改判 statistical + citation_only：
            # 只引用，不冒充已重算。
            # 2026-07-02 引擎泛化排查（stage 2 接通调研）：起初核对 build_alignment_summary()
            # 读取的 AlignmentQC.xls 汇总表，确认那条路径确实拿不到原始分量。但接着核对
            # project_expert_tool_service.run_alignment_expert() 解析的 *.nrf_pbc.txt
            # （calc_nrf_pbc.sh 产出，CUT&Tag/ChIP-seq/CUT&RUN/ATAC-seq 项目里真实存在），
            # 发现它本来就带 Total_Fragments/Distinct_Locations 两列原始计数，
            # NRF = Distinct_Locations / Total_Fragments 是普通两操作数除法，根本不是
            # "统计量"，是这里的分类选错了 recompute 形态。已接通：run_alignment_expert()
            # 现在会把这两列作为 numerator_value/denominator_value 写入 evidence_card，
            # 改判 recompute=ratio + verifier_contract=strict_formula_recalculation。
            # 没有原始计数的证据源（如 AlignmentQC.xls 汇总表）走 ratio 分支的
            # inputs_missing 兜底，如实报 cited_not_recalculated，不会假装重算过。
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "pbc1": {
            "label": "PBC1",
            "label_zh": "PBC1 文库复杂度",
            "unit": "fraction",
            "display_unit": "",
            "value_scale": "fraction",
            "valid_range": [0.0, 1.0],
            "formula": "locations_with_exactly_one_fragment / distinct_genomic_locations",
            "numerator": "locations with exactly one fragment",
            "denominator": "distinct genomic locations",
            "source_scale": "fraction",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
            # 2026-07-02 止血：同 nrf，需要位点分布，汇总表通常拿不到，改判 statistical +
            # citation_only，不再冒充 strict。
            # 2026-07-02 引擎泛化排查：结论同 nrf，且已接通——*.nrf_pbc.txt 里
            # Locations_1read/Distinct_Locations 两列原始计数本来就存在，
            # PBC1 = Locations_1read / Distinct_Locations 是普通除法。
            # run_alignment_expert() 现在会传入真实 numerator_value/denominator_value，
            # 改判 recompute=ratio + verifier_contract=strict_formula_recalculation。
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "pbc2": {
            "label": "PBC2",
            "label_zh": "PBC2 文库复杂度",
            "unit": "ratio",
            "display_unit": "",
            "value_scale": "ratio",
            "valid_range": [0.0, None],
            "formula": "locations_with_exactly_one_fragment / locations_with_exactly_two_fragments",
            "numerator": "locations with exactly one fragment",
            "denominator": "locations with exactly two fragments",
            "source_scale": "ratio",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
            # 2026-07-02 止血：PBC2 结构上仍是两个位点计数之比，但汇总表通常只给最终值，
            # 拿不到两个计数列，改判 statistical + citation_only。
            # 2026-07-02 引擎泛化排查：build_alignment_summary()（AlignmentQC.xls 汇总表）
            # 确实只有最终值，但 *.nrf_pbc.txt 里 Locations_1read/Locations_2reads 两列
            # 原始计数本来就存在，PBC2 = Locations_1read / Locations_2reads 是普通除法。
            # run_alignment_expert() 现在会传入真实 numerator_value/denominator_value，
            # 改判 recompute=ratio + verifier_contract=strict_formula_recalculation。
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "spikein_mapped_reads": {
            "label": "Spike-in mapped reads",
            "label_zh": "Spike-in mapped reads",
            "unit": "reads",
            "display_unit": "reads",
            "value_scale": "count",
            "valid_range": [0.0, None],
            "formula": "count(reads_mapped_to_spikein_reference)",
            "numerator": "reads mapped to spike-in reference",
            "denominator": "",
            "source_scale": "count",
            "assay_scope": ["cuttag", "chipseq", "cutrun"],
            # 2026-07-02 止血：这是 count()，不是除法，此前默认档把它当 strict 重算，
            # 但压根没有分子分母可算。改判 display：只展示，不做数值重算校验。
            "recompute": "display",
            "verifier_contract": "display_value_only",
        },
        "spikein_unique_mapping_rate_percent": {
            "label": "Spike-in unique mapping rate",
            "label_zh": "Spike-in 唯一比对率",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "unique_spikein_mapped_reads / spikein_alignment_input_reads * 100",
            "numerator": "uniquely mapped spike-in reads",
            "denominator": "spike-in alignment input reads",
            "source_scale": "percent",
            "assay_scope": ["cuttag", "chipseq", "cutrun"],
            # 2026-07-02 止血：结构上是 a/b×100，补齐显式声明，消灭默认 strict 陷阱。
            "recompute": "ratio",
            "verifier_contract": "strict_formula_recalculation",
        },
        "spikein_scaling_factor": {
            "label": "Spike-in scaling factor",
            "label_zh": "Spike-in scaling factor",
            "unit": "factor",
            "display_unit": "",
            "value_scale": "number",
            "valid_range": [0.0, None],
            "formula": "project_defined_normalization_constant",
            "numerator": "",
            "denominator": "",
            "source_scale": "number",
            "assay_scope": ["cuttag", "chipseq", "cutrun"],
            # 2026-07-02 止血：这是项目定义的常数（project_defined_normalization_constant），
            # 没有可算公式，此前默认档把它当 strict 重算是彻底的名不副实。改判 constant：
            # 不可重算，只能引用来源。
            "recompute": "constant",
            "verifier_contract": "citation_only",
        },
        "correlation": {
            "label": "Sample correlation",
            "label_zh": "样本信号相关性",
            "unit": "coefficient",
            "display_unit": "",
            "value_scale": "coefficient",
            "valid_range": [-1.0, 1.0],
            "formula": "correlation(signal_vector_a, signal_vector_b)",
            "numerator": "",
            "denominator": "paired signal features/bins",
            "source_scale": "coefficient",
            "assay_scope": ["all"],
            # 2026-07-02 止血（truth_layer_recompute_generalization_plan.md §8/§11 step1）：
            # correlation 之前声明 strict_formula_recalculation，但汇总表里
            # numerator/denominator 从未真正传入，normalize() 静默跳过重算却仍判 valid，
            # 名不副实。改判 statistical + citation_only：只引用汇总表已算好的系数，
            # 不再冒充"已重算校验"。
            # Stage 2 补充：statistical_fn 先给 pearson 默认值——`_run_statistical()`
            # 只在调用方明确传了 statistical_vectors（成对信号向量）时才会用到它；
            # 具体项目用 Spearman 还是 Pearson 由 formula_variants 里的两个变体区分，
            # 调用方按需选择，这里的默认值只是"没指定变体时"的兜底，不影响任何现有
            # 调用方（Stage 3 才会真正接上调用方传向量进来）。
            # 2026-07-02 引擎泛化排查（接通调研，结论：暂不可行）：核对了
            # read_correlation_rows() 解析的 spearman_corr_readcounts.tab——这是一份
            # 样本×样本的系数矩阵，每个格子已经是算好的相关系数，不是参与运算前的
            # 原始信号向量（per-bin/per-gene）。也就是说这份文件本身就是相关性计算的
            # "结果"，不是"输入"，无法反推出 statistical_vectors。当前代码库里没有任何
            # parser 读取过 deepTools --outRawCounts 之类的原始 bin 矩阵。因此 correlation
            # 目前和 nrf/pbc1/pbc2 一样，停在 citation_only 是诚实的终态，不是待办；只有
            # 未来新增能读出原始向量的 parser 时才具备升级条件。
            "recompute": "statistical",
            "statistical_fn": "pearson",
            "verifier_contract": "citation_only",
        },
        "peak_count": {
            "label": "Peak count",
            "label_zh": "Peak 数量",
            "unit": "peaks",
            "display_unit": "peaks",
            "value_scale": "count",
            "valid_range": [0.0, None],
            "formula": "count(called_peak_records)",
            "numerator": "called peak records",
            "denominator": "",
            "source_scale": "count",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
            "recompute": "display",
            "verifier_contract": "display_value_only",
        },
        "sequencing_depth": {
            "label": "Sequencing depth",
            "label_zh": "测序深度",
            "unit": "reads",
            "display_unit": "reads",
            "value_scale": "count",
            "valid_range": [0.0, None],
            "formula": "count(input_or_clean_reads)",
            "numerator": "input or clean reads",
            "denominator": "",
            "source_scale": "count",
            "assay_scope": ["all"],
            "recompute": "display",
            "verifier_contract": "display_value_only",
        },
        "control_binding_status": {
            "label": "Control binding status",
            "label_zh": "对照可用性与绑定状态",
            "unit": "categorical",
            "display_unit": "",
            "value_scale": "categorical",
            "valid_range": None,
            "formula": "qualitative_assessment_of_control_background_binding",
            "numerator": "",
            "denominator": "",
            "source_scale": "categorical",
            "assay_scope": ["cuttag", "chipseq", "cutrun"],
            "recompute": "qualitative",
            "verifier_contract": "non_numeric_design_status",
        },
        # ── RNA-seq 专属指标 ───────────────────────────────────────────────
        "mrna_ratio_percent": {
            "label": "mRNA reads ratio",
            "label_zh": "mRNA reads 比例",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "mRNA_reads / total_mapped_reads * 100",
            "numerator": "mRNA aligned reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "recompute": "ratio",
            "verifier_contract": "citation_only",
        },
        "rrna_ratio_percent": {
            "label": "rRNA reads ratio",
            "label_zh": "rRNA reads 比例",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "rRNA_reads / total_mapped_reads * 100",
            "numerator": "rRNA aligned reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "recompute": "ratio",
            "verifier_contract": "citation_only",
        },
        "silva_total_ratio_percent": {
            "label": "Silva rRNA total ratio",
            "label_zh": "Silva rRNA 总比例",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "silva_rRNA_hits / sampled_reads * 100",
            "numerator": "SILVA rRNA blast hits",
            "denominator": "sampled reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "recompute": "ratio",
            "verifier_contract": "display_value_only",
        },
        "exon_ratio_percent": {
            "label": "Exon reads ratio",
            "label_zh": "外显子 reads 比例",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "exon_reads / total_mapped_reads * 100",
            "numerator": "exon aligned reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "recompute": "ratio",
            "verifier_contract": "citation_only",
        },
        "intronic_ratio_percent": {
            "label": "Intronic reads ratio",
            "label_zh": "内含子 reads 比例",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "intronic_reads / total_mapped_reads * 100",
            "numerator": "intronic reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "recompute": "ratio",
            "verifier_contract": "citation_only",
        },
        "intergenic_ratio_percent": {
            "label": "Intergenic reads ratio",
            "label_zh": "基因间区 reads 比例",
            "unit": "%",
            "display_unit": "%",
            "value_scale": "percent",
            "valid_range": [0.0, 100.0],
            "formula": "intergenic_reads / total_mapped_reads * 100",
            "numerator": "intergenic reads",
            "denominator": "total mapped reads",
            "source_scale": "percent",
            "assay_scope": ["rnaseq"],
            "recompute": "ratio",
            "verifier_contract": "citation_only",
        },
        "detected_gene_count": {
            "label": "Detected genes",
            "label_zh": "检测到基因数",
            "unit": "genes",
            "display_unit": "genes",
            "value_scale": "count",
            "valid_range": [0.0, None],
            "formula": "count(genes_with_expression_above_zero)",
            "numerator": "genes with non-zero expression",
            "denominator": "",
            "source_scale": "count",
            "assay_scope": ["rnaseq"],
            "recompute": "display",
            "verifier_contract": "display_value_only",
        },
        # ── 2026-07-06 fact_packet 增强补录：此前只在 answer_quality_service /
        # claim_service 的本地 METRIC_LABELS 里出现过标签，metric_schema_service
        # 注册表里完全没有条目。三者都是 project_expert_tool_service.run_enrichment_
        # expert() 里直接读汇总表的 median/mean 展示值（median/mean fragment size、
        # median/mean peak width、TSS enrichment score），源头拿不到可重算的分子分母，
        # 定性上和 peak_count/sequencing_depth 同类，recompute=display、
        # verifier_contract=display_value_only。
        "peak_width": {
            "label": "Peak width",
            "label_zh": "Peak 宽度",
            "unit": "bp",
            "display_unit": "bp",
            "value_scale": "number",
            "valid_range": [0.0, None],
            "formula": "median/mean interval width across called peaks",
            "numerator": "",
            "denominator": "called peak intervals",
            "source_scale": "number",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
            "recompute": "display",
            "verifier_contract": "display_value_only",
        },
        "tss_enrichment": {
            "label": "TSS enrichment",
            "label_zh": "TSS enrichment",
            "unit": "score",
            "display_unit": "",
            "value_scale": "number",
            "valid_range": [0.0, None],
            "formula": "normalized TSS-centered signal relative to flanking background",
            "numerator": "",
            "denominator": "TSS flanking background signal",
            "source_scale": "number",
            "assay_scope": ["cuttag", "chipseq", "cutrun", "atacseq"],
            "recompute": "display",
            "verifier_contract": "display_value_only",
        },
        "fragment_size": {
            "label": "Fragment size",
            "label_zh": "Fragment size",
            "unit": "bp",
            "display_unit": "bp",
            "value_scale": "number",
            "valid_range": [0.0, None],
            "formula": "median/mean fragment size reported by fragment-size QC",
            "numerator": "",
            "denominator": "fragments included in the size summary",
            "source_scale": "number",
            "assay_scope": ["all"],
            "recompute": "display",
            "verifier_contract": "display_value_only",
        },
    }

    # ── Phase 0：detection_signature（供 Phase 1 文件/字段发现层做启发式匹配） ──
    # 只登记"这个指标常见于什么样的表头/字段命名"，不是穷举，探索层命中即可加分，
    # 不命中不影响现有关键词表路径（QUESTION_FILE_HINTS / TARGET_METRIC_FILE_HINTS 不变）。
    _DETECTION_SIGNATURES: dict[str, list[str]] = {
        "adapter_percent": ["adapter", "adapter_percent", "adapter content", "接头"],
        "clean_read_retention_percent": ["retention", "clean reads", "written_pairs", "trimming", "过滤保留"],
        "frip_ratio": ["frip", "reads in peaks", "reads_in_peaks", "peak ratio"],
        "mapping_rate_percent": ["mapping rate", "mapped_reads", "overall alignment rate", "总体比对率"],
        "unique_mapping_rate_percent": ["unique mapping", "uniquely mapped", "unique_mapped_reads"],
        "duplicate_rate_percent": ["duplicate", "dup rate", "duplication", "重复率"],
        "picard_duplicate_pair_rate_percent": ["percent_duplication", "picard", "duplicate pair"],
        "mt_rate_percent": ["mt_rate", "chrm", "mitochond", "organelle", "线粒体"],
        "q20_ratio": ["q20"],
        "q30_ratio": ["q30"],
        "nrf": ["nrf", "non redundant fraction"],
        "pbc1": ["pbc1"],
        "pbc2": ["pbc2"],
        "spikein_mapped_reads": ["spikein", "spike-in", "spike_in"],
        "spikein_unique_mapping_rate_percent": ["spikein", "spike-in unique", "spike_in_unique"],
        "spikein_scaling_factor": ["scaling factor", "spikein_scaling", "normalization factor"],
        "correlation": ["correlation", "spearman", "pearson", "相关性"],
        "peak_count": ["peak count", "narrowpeak", "broadpeak", "num_peaks"],
        "sequencing_depth": ["sequencing depth", "raw reads", "total reads", "clean reads"],
        "control_binding_status": ["control", "igg", "input control"],
        "mrna_ratio_percent": ["mrna ratio", "mrna_reads"],
        "rrna_ratio_percent": ["rrna ratio", "rrna_reads"],
        "silva_total_ratio_percent": ["silva"],
        "exon_ratio_percent": ["exon"],
        "intronic_ratio_percent": ["intron"],
        "intergenic_ratio_percent": ["intergenic"],
        "detected_gene_count": ["detected genes", "gene count", "genes_expressed"],
        "peak_width": ["peak width", "peak_width", "峰宽"],
        "tss_enrichment": ["tss enrichment", "tss_enrichment", "tss score", "tss_score"],
        "fragment_size": ["fragment size", "fragment_size", "insert size", "片段长度"],
    }

    # ── Phase 0：formula_variants（人工预审的公式变体，见 2.2 节） ──────────
    # 仅对 strict_formula_recalculation 指标登记；代码语义解析 agent（Phase 1.1，
    # 本次不实施）未来只能在这个人工预审名单里"分类匹配"，不能自行创造新变体。
    # `applicable_assays` 用 assay_scope 同一套词汇（cuttag/chipseq/cutrun/atacseq/
    # rnaseq/all）。同一个 metric_id 下不同变体可能只适用于部分 assay——比如 CUT&Tag/
    # CUT&RUN 和 ChIP-seq/ATAC-seq 对 frip_ratio 的分母口径不同，缺了这个字段时代码
    # 语义解析 agent 会拿别的 assay 的变体去误判当前项目的公式，见
    # project_code_semantics_service.py 的 _classify_variant()：它现在会先按
    # applicable_assays 过滤候选变体，再匹配分子/分母变量名。未标注的变体默认按
    # "all" 处理，兼容旧数据。
    _FORMULA_VARIANTS: dict[str, list[dict[str, Any]]] = {
        "mapping_rate_percent": [
            {
                "variant_id": "unique_only",
                "label": "仅统计唯一比对 reads",
                "numerator_vars": ["uniquely_mapped_reads", "unique_mapped_reads"],
                "denominator_vars": ["total_reads", "alignment_input_reads"],
                "note": "部分 SOP 版本的 mapping_rate 只计入唯一比对，不含多重比对",
                "applicable_assays": ["all"],
            },
            {
                "variant_id": "unique_plus_multi",
                "label": "唯一比对 + 多重比对合计",
                "numerator_vars": ["mapped_reads", "overall_aligned_reads"],
                "denominator_vars": ["total_reads", "alignment_input_reads"],
                "note": "bowtie2/hisat2 默认 overall alignment rate 口径",
                "applicable_assays": ["all"],
            },
        ],
        "unique_mapping_rate_percent": [
            {
                "variant_id": "unique_only",
                "label": "唯一比对 reads / 总 reads",
                "numerator_vars": ["uniquely_mapped_reads"],
                "denominator_vars": ["total_reads", "alignment_input_reads"],
                "note": "默认口径",
                "applicable_assays": ["all"],
            },
        ],
        "frip_ratio": [
            {
                "variant_id": "usable_fragments",
                "label": "peak 内 reads / 可用比对 reads",
                "numerator_vars": ["reads_in_peaks"],
                "denominator_vars": ["usable_mapped_reads", "usable_fragments"],
                "note": "默认口径，MACS3/featureCounts 常见输出",
                "applicable_assays": ["chipseq", "atacseq"],
            },
            {
                "variant_id": "dedup_fragments",
                "label": "peak 内 reads / 去重后比对 fragments",
                "numerator_vars": ["reads_in_peaks"],
                "denominator_vars": ["dedup_mapped_fragments"],
                "note": "部分 CUT&Tag/CUT&RUN SOP 用去重后 fragment 数做分母",
                "applicable_assays": ["cuttag", "cutrun"],
            },
        ],
        "correlation": [
            {
                "variant_id": "spearman",
                "label": "Spearman 相关系数",
                "numerator_vars": [],
                "denominator_vars": [],
                "note": "对信号分箱做秩相关",
                "applicable_assays": ["all"],
            },
            {
                "variant_id": "pearson",
                "label": "Pearson 相关系数",
                "numerator_vars": [],
                "denominator_vars": [],
                "note": "对信号分箱做线性相关",
                "applicable_assays": ["all"],
            },
        ],
    }

    @classmethod
    def _apply_phase0_defaults(cls) -> None:
        """为每条指标补齐 applicable_assays / detection_signature / formula_variants。

        纯附加操作，不修改任何已有阈值/公式/单位/verifier_contract 字段，
        保证 Phase 0 是接口兼容的纯重构（见方案第 3 节验收标准）。
        """
        for metric_id, schema in cls.METRICS.items():
            schema.setdefault(
                "applicable_assays",
                list(schema.get("assay_scope") or ["all"]),
            )
            schema.setdefault(
                "detection_signature",
                list(cls._DETECTION_SIGNATURES.get(metric_id, [])),
            )
            schema.setdefault(
                "formula_variants",
                deepcopy(cls._FORMULA_VARIANTS.get(metric_id, [])),
            )

    ALIASES = {
        "frip": "frip_ratio",
        "mapping": "mapping_rate_percent",
        "mapping_rate": "mapping_rate_percent",
        "unique": "unique_mapping_rate_percent",
        "unique_mapping_rate": "unique_mapping_rate_percent",
        "duplicate": "duplicate_rate_percent",
        "duplication": "duplicate_rate_percent",
        "picard_duplicate_pair_rate_percent": "duplicate_rate_percent",
        "mt_ratio": "mt_rate_percent",
        "chrmt_pt_rate_percent": "mt_rate_percent",
        "spikein_unique_rate": "spikein_unique_mapping_rate_percent",
        # RNA-seq aliases
        "mrna_ratio": "mrna_ratio_percent",
        "rrna_ratio": "rrna_ratio_percent",
        "silva_total_ratio": "silva_total_ratio_percent",
        "silva_ratio": "silva_total_ratio_percent",
        "silva": "silva_total_ratio_percent",
        "exon_ratio": "exon_ratio_percent",
        "intronic_ratio": "intronic_ratio_percent",
        "intergenic_ratio": "intergenic_ratio_percent",
        "gene_count": "detected_gene_count",
        "detected_genes": "detected_gene_count",
    }

    # ── Phase 0：运行时可追加的注册表 ──────────────────────────────────────
    # `METRICS` 本身保持不变（人工维护基线）；`_REGISTRY` 是它的可变副本，
    # 所有查询方法都读 `_registry()`，`register_metric()` 是唯一写入口。
    _REGISTRY: dict[str, dict[str, Any]] | None = None
    _REGISTRY_LOCK = threading.Lock()

    @classmethod
    def _registry(cls) -> dict[str, dict[str, Any]]:
        if cls._REGISTRY is None:
            with cls._REGISTRY_LOCK:
                if cls._REGISTRY is None:
                    cls._REGISTRY = deepcopy(cls.METRICS)
        return cls._REGISTRY

    @classmethod
    def register_metric(
        cls,
        metric_id: str,
        schema: dict[str, Any],
        *,
        overwrite: bool = False,
    ) -> bool:
        """运行时追加一条新指标定义。

        用于 Phase 1.5 候选指标转正流程：candidate_metrics 审核通过后，把补全了
        `verifier_contract` / 单位 / 适用实验类型的正式指标写入这里。此前不会被
        除测试外的任何代码路径调用，不影响现有指标行为。返回 False 表示
        metric_id 已存在且 overwrite=False，未写入。
        """
        canonical = str(metric_id or "").strip().lower()
        canonical = cls.ALIASES.get(canonical, canonical)
        if not canonical:
            return False
        registry = cls._registry()
        with cls._REGISTRY_LOCK:
            if canonical in registry and not overwrite:
                return False
            entry = deepcopy(schema)
            entry.setdefault("applicable_assays", list(entry.get("assay_scope") or ["all"]))
            entry.setdefault("detection_signature", [])
            entry.setdefault("formula_variants", [])
            registry[canonical] = entry
        return True

    @classmethod
    def all_metric_ids(cls) -> list[str]:
        return list(cls._registry().keys())

    @classmethod
    def canonical_id(cls, metric_id: Any) -> str:
        normalized = str(metric_id or "").strip().lower()
        return cls.ALIASES.get(normalized, normalized)

    @classmethod
    def detect_metrics_in_text(cls, text: str) -> list[str]:
        """扫描一段原始文本（通常是用户问题），返回被字面提及的已注册指标 canonical_id 列表。

        2026-07-02 新增（project_analysis_phase1.5_auto_promotion_revision.md 回归排查）：
        `_select_evidence_files` 里的 target_metrics 此前只能来自 question_type 分类表
        （metric_by_question_type）或 assay 默认指标集（_ASSAY_DEFAULT_METRICS），两者都是
        有限枚举，问题里直接点名一个不在这两张表里的已注册指标（比如 RNA-seq 的
        "Silva_total_ratio(%)"）时，target_metrics 永远凑不出这个指标，连带下游 Phase 1
        文件发现探索兜底也不会针对它触发（探索只在 unresolved_metrics 非空时跑）。
        这里复用已经维护好的 detection_signature（Phase 0，供文件/字段发现层用）和
        ALIASES 作为问题文本侧的匹配词表，不新增维护成本；只做"字面提及即命中"的
        朴素子串匹配，不做真值判断——是否真的读到证据、指标是否真实存在，仍由下游
        文件发现 + parser + verifier_contract 决定，这里最多让本来会被无声丢弃的
        指标问题多一次探索机会。
        """
        normalized = str(text or "").strip().lower()
        if not normalized:
            return []
        matched: list[str] = []
        for metric_id, schema in cls._registry().items():
            tokens = schema.get("detection_signature") or cls._DETECTION_SIGNATURES.get(metric_id, [])
            for token in tokens:
                token_norm = str(token or "").strip().lower()
                if token_norm and token_norm in normalized:
                    matched.append(metric_id)
                    break
        for alias, canonical in cls.ALIASES.items():
            alias_norm = str(alias or "").strip().lower()
            if alias_norm and alias_norm in normalized and canonical not in matched:
                matched.append(canonical)
        return list(dict.fromkeys(matched))

    @classmethod
    def get(cls, metric_id: Any) -> dict[str, Any]:
        canonical = cls.canonical_id(metric_id)
        schema = deepcopy(cls._registry().get(canonical, {}))
        if schema:
            schema["metric_id"] = canonical
            schema["schema_version"] = cls.SCHEMA_VERSION
        return schema

    @classmethod
    def export_schema(cls) -> dict[str, Any]:
        return {
            "schema_version": cls.SCHEMA_VERSION,
            "metrics": {
                metric_id: {**deepcopy(schema), "metric_id": metric_id}
                for metric_id, schema in cls._registry().items()
            },
        }

    # 2026-07-02 止血（truth_layer_recompute_generalization_plan.md §4.2）：
    # verifier_contract() 的默认值此前硬编码为 strict_formula_recalculation，导致任何
    # 未显式声明 contract 的指标都被静默当作"严格重算"对待（见方案 §1.3）。改为按
    # recompute 方法推导默认值——只在指标既没有 verifier_contract 又没有 recompute
    # 声明时才会用到（理论上不应再发生，因为本次已给全部已知指标补齐显式声明；
    # 这里只是兜底，防止未来新增指标又掉进同一个陷阱）。
    _CONTRACT_BY_RECOMPUTE: dict[str, str] = {
        "ratio": "strict_formula_recalculation",
        "expression": "strict_formula_recalculation",
        "aggregate": "strict_formula_recalculation",
        "statistical": "citation_only",
        "constant": "citation_only",
        "display": "display_value_only",
        "qualitative": "non_numeric_design_status",
    }

    @classmethod
    def verifier_contract(cls, metric_id: Any) -> str:
        schema = cls.get(metric_id)
        explicit = schema.get("verifier_contract")
        if explicit:
            return str(explicit)
        recompute_method = str(schema.get("recompute") or "ratio")
        return cls._CONTRACT_BY_RECOMPUTE.get(recompute_method, "strict_formula_recalculation")

    # ── Stage 2（truth_layer_recompute_generalization_plan.md §5）：safe_eval ──
    # 确定性、无副作用的表达式求值器，绝不用 Python eval。只允许 §5 列出的白名单
    # AST 节点/运算符/函数；任何越界写法（属性访问、下标、推导式、__ 开头的名字、
    # 白名单外的函数调用等）一律抛 ValueError，由调用方判定"这个表达式不可安全求值"。
    _SAFE_EVAL_BINOPS = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
    }
    _SAFE_EVAL_UNARYOPS = {
        ast.USub: operator.neg,
        ast.UAdd: operator.pos,
    }
    _SAFE_EVAL_FUNCS = {
        "min": min,
        "max": max,
        "abs": abs,
    }

    @classmethod
    def safe_eval(cls, expr: str, mapped_vars: dict[str, float]) -> float:
        """在人工预审的算术白名单内对 `expr` 求值，变量只从 `mapped_vars` 取。

        用于 recompute == "expression" 的指标（如 `a/(b+c)`、`(a-b)/a`）。不属于
        `ratio` 那种两操作数除法，但仍然是可确定性验证的算术表达式。
        """
        try:
            tree = ast.parse(str(expr or ""), mode="eval")
        except SyntaxError as exc:
            raise ValueError(f"unsafe_expression_syntax: {exc}") from exc
        return cls._safe_eval_node(tree.body, mapped_vars)

    @classmethod
    def _safe_eval_node(cls, node: ast.AST, mapped_vars: dict[str, float]) -> float:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("unsafe_expression_constant")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in mapped_vars:
                raise ValueError(f"unsafe_expression_missing_var:{node.id}")
            value = mapped_vars[node.id]
            if value is None:
                raise ValueError(f"unsafe_expression_missing_var:{node.id}")
            return float(value)
        if isinstance(node, ast.BinOp) and type(node.op) in cls._SAFE_EVAL_BINOPS:
            left = cls._safe_eval_node(node.left, mapped_vars)
            right = cls._safe_eval_node(node.right, mapped_vars)
            return cls._SAFE_EVAL_BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in cls._SAFE_EVAL_UNARYOPS:
            operand = cls._safe_eval_node(node.operand, mapped_vars)
            return cls._SAFE_EVAL_UNARYOPS[type(node.op)](operand)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in cls._SAFE_EVAL_FUNCS
            and not node.keywords
        ):
            args = [cls._safe_eval_node(arg, mapped_vars) for arg in node.args]
            return float(cls._SAFE_EVAL_FUNCS[node.func.id](*args))
        raise ValueError(f"unsafe_expression_node:{type(node).__name__}")

    # ── Stage 2：aggregate/statistical 求值器（纯函数，无外部依赖） ──────────
    _AGGREGATE_FUNCS = {
        "count": lambda values: float(len(values)),
        "sum": lambda values: float(sum(values)),
        "mean": lambda values: float(statistics.mean(values)),
        "median": lambda values: float(statistics.median(values)),
    }

    @classmethod
    def _run_aggregate(cls, fn_name: str, values: list[float]) -> float:
        fn = cls._AGGREGATE_FUNCS.get(str(fn_name or ""))
        if fn is None:
            raise ValueError(f"unsupported_aggregate_fn:{fn_name}")
        cleaned = [float(v) for v in values if v is not None]
        if not cleaned:
            raise ValueError("aggregate_no_values")
        return fn(cleaned)

    @staticmethod
    def _pearson(vector_a: list[float], vector_b: list[float]) -> float:
        if len(vector_a) != len(vector_b) or len(vector_a) < 2:
            raise ValueError("statistical_input_length_mismatch")
        mean_a = statistics.mean(vector_a)
        mean_b = statistics.mean(vector_b)
        cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(vector_a, vector_b))
        var_a = sum((a - mean_a) ** 2 for a in vector_a)
        var_b = sum((b - mean_b) ** 2 for b in vector_b)
        denom = math.sqrt(var_a * var_b)
        if denom == 0:
            raise ValueError("statistical_zero_variance")
        return cov / denom

    @classmethod
    def _spearman(cls, vector_a: list[float], vector_b: list[float]) -> float:
        if len(vector_a) != len(vector_b) or len(vector_a) < 2:
            raise ValueError("statistical_input_length_mismatch")

        def _ranks(values: list[float]) -> list[float]:
            order = sorted(range(len(values)), key=lambda i: values[i])
            ranks = [0.0] * len(values)
            i = 0
            while i < len(order):
                j = i
                while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                    j += 1
                avg_rank = (i + j) / 2.0 + 1.0
                for k in range(i, j + 1):
                    ranks[order[k]] = avg_rank
                i = j + 1
            return ranks

        return cls._pearson(_ranks(vector_a), _ranks(vector_b))

    @classmethod
    def _run_statistical(cls, fn_name: str, vectors: tuple[list[float], list[float]]) -> float:
        name = str(fn_name or "").strip().lower()
        vector_a, vector_b = vectors
        if name == "pearson":
            return cls._pearson(list(vector_a), list(vector_b))
        if name == "spearman":
            return cls._spearman(list(vector_a), list(vector_b))
        raise ValueError(f"unsupported_statistical_fn:{fn_name}")

    @classmethod
    def normalize(
        cls,
        metric_id: Any,
        raw_value: Any,
        *,
        source_field: str = "",
        source_scale: str = "",
        numerator: Any = None,
        denominator: Any = None,
        expression_vars: dict[str, Any] | None = None,
        aggregate_values: list[Any] | None = None,
        statistical_vectors: tuple[list[Any], list[Any]] | None = None,
    ) -> dict[str, Any]:
        """`expression_vars`/`aggregate_values`/`statistical_vectors` 是 Stage 2
        （truth_layer_recompute_generalization_plan.md §5/§6）新增的可选入参，只有
        指标声明了对应 `recompute` 形态且调用方传入了这些数据时才会被使用。现有
        所有调用方都不传，行为与 Stage 1 完全一致——这是纯加法式的引擎能力扩展，
        不是行为变更。"""
        canonical = cls.canonical_id(metric_id)
        schema = cls.get(canonical)
        parsed = cls._number(raw_value)
        issues: list[dict[str, Any]] = []
        if parsed is None:
            return {
                "metric_id": canonical,
                "value": None,
                "display_value": "-",
                "input_scale": "unknown",
                "conversion": "none",
                "valid": False,
                "issues": [{"rule": "non_numeric_metric_value", "raw_value": raw_value}],
                "schema": schema,
                "recompute_status": "not_applicable",
                "trust_level": "display_only",
            }

        input_scale = (
            str(source_scale).strip().lower()
            or cls._input_scale(raw_value, source_field, schema)
        )
        expected_scale = str(schema.get("value_scale") or "number")
        value = parsed
        conversion = "identity"
        if expected_scale == "fraction" and input_scale == "percent":
            value = parsed / 100.0
            conversion = "percent_to_fraction"
        elif expected_scale == "percent" and input_scale == "fraction":
            value = parsed * 100.0
            conversion = "fraction_to_percent"
        elif expected_scale == "percent" and parsed <= 1.0 and input_scale == "number":
            value = parsed * 100.0
            conversion = "implicit_fraction_to_percent"

        # 2026-07-02 止血（truth_layer_recompute_generalization_plan.md §6）：
        # recompute 方法目前只有 "ratio" 有真实求值器（其余形态的求值器留给引擎泛化
        # 阶段/stage 2 实现）。这一段的算术与容差逻辑与改造前逐字一致——只是把结果
        # 归入统一的 recompute_status/trust_level 出口，不再是"算了就算，没算就沉默"。
        contract = cls.verifier_contract(canonical)
        recompute_method = str(schema.get("recompute") or "ratio")
        numerator_value = cls._number(numerator)
        denominator_value = cls._number(denominator)
        recompute_status = "not_applicable"
        trust_level = "display_only"

        if recompute_method == "ratio":
            if numerator_value is not None and denominator_value not in (None, 0):
                recalculated = numerator_value / denominator_value
                if expected_scale == "percent":
                    recalculated *= 100.0
                rounding_tolerance = (
                    5e-5
                    if expected_scale == "fraction"
                    else 5e-3
                    if expected_scale == "percent"
                    else 1e-6
                )
                tolerance = max(rounding_tolerance, abs(value) * 1e-3)
                if abs(recalculated - value) > tolerance:
                    issues.append(
                        {
                            "rule": "formula_recalculation_mismatch",
                            "observed": value,
                            "recalculated": recalculated,
                        }
                    )
                recompute_status = "recomputed"
                trust_level = "recalculated"
            elif contract == "strict_formula_recalculation":
                recompute_status = "inputs_missing"
                trust_level = "cited_not_recalculated"
            else:
                # 指标没有显式声明 recompute（沿用 "ratio" 默认值），但它的 contract
                # 本来就不是 strict（比如 peak_count 这类纯 count 指标）——没传
                # numerator/denominator 是正常情况，不是"该算却没算成"，标 not_applicable
                # 而不是 inputs_missing，避免把"天生只读"误报成"算不了"。
                recompute_status = "not_applicable"
                trust_level = "display_only"
        elif recompute_method == "expression" and expression_vars:
            # Stage 2（§5/§6）：多操作数算术表达式，如 a/(b+c)、(a-b)/a。只在调用方
            # 传了 expression_vars 且变量齐全时才尝试；变量名/表达式来自人工预审的
            # formula_variants 或代码语义解析结果，不是这里凭空发明的。
            try:
                mapped = {k: cls._number(v) for k, v in expression_vars.items()}
                if any(v is None for v in mapped.values()):
                    raise ValueError("expression_var_missing")
                recalculated = cls.safe_eval(str(schema.get("expression") or ""), mapped)
                # `expression_scale` 只用于"表达式本身算出来是 fraction、还没转成
                # percent"的情况（比如 expression="p"，值域 0~1）。如果 expression
                # 文本里已经自带 ×100（比如 "p * 100"），declare 时绝对不要再标
                # expression_scale="fraction"，否则这里会重复换算，把值再乘一次
                # 100——两者是互斥的，公式作者只能选一种方式表达"转成 percent"。
                if expected_scale == "percent" and str(schema.get("expression_scale") or "") == "fraction":
                    recalculated *= 100.0
                tolerance = max(5e-3, abs(value) * 1e-3)
                if abs(recalculated - value) > tolerance:
                    issues.append(
                        {
                            "rule": "formula_recalculation_mismatch",
                            "observed": value,
                            "recalculated": recalculated,
                        }
                    )
                recompute_status = "recomputed"
                trust_level = "recalculated"
            except (ValueError, ZeroDivisionError, TypeError):
                recompute_status = "inputs_missing" if contract == "strict_formula_recalculation" else "not_applicable"
                trust_level = "cited_not_recalculated" if contract == "strict_formula_recalculation" else "display_only"

        elif recompute_method == "aggregate" and aggregate_values:
            # Stage 2：对某一列原始数据跑 count/sum/mean/median，只在调用方传了完整
            # 原始列时才尝试；只有汇总标量时 aggregate_values 应为空，走下面的兜底。
            try:
                recalculated = cls._run_aggregate(str(schema.get("aggregate_fn") or ""), list(aggregate_values))
                tolerance = max(1e-6, abs(value) * 1e-3)
                if abs(recalculated - value) > tolerance:
                    issues.append(
                        {
                            "rule": "formula_recalculation_mismatch",
                            "observed": value,
                            "recalculated": recalculated,
                        }
                    )
                recompute_status = "recomputed"
                trust_level = "recalculated"
            except (ValueError, TypeError, statistics.StatisticsError):
                recompute_status = "inputs_missing" if contract == "strict_formula_recalculation" else "not_applicable"
                trust_level = "cited_not_recalculated" if contract == "strict_formula_recalculation" else "display_only"

        elif recompute_method == "statistical" and statistical_vectors:
            # Stage 2：相关系数等需要原始向量/分布的指标。汇总表通常拿不到向量，
            # 只有调用方明确传了成对的信号向量时才尝试重算——这正是 §7.1 说的
            # "同一 metric_id 因数据源不同在两档间动态切换"：这次算成了就是
            # recalculated，declared contract（citation_only）本身不因此改写。
            try:
                vec_a = [cls._number(v) for v in statistical_vectors[0]]
                vec_b = [cls._number(v) for v in statistical_vectors[1]]
                if any(v is None for v in vec_a) or any(v is None for v in vec_b):
                    raise ValueError("statistical_vector_missing_values")
                recalculated = cls._run_statistical(str(schema.get("statistical_fn") or ""), (vec_a, vec_b))
                tolerance = max(5e-3, abs(value) * 2e-2)
                if abs(recalculated - value) > tolerance:
                    issues.append(
                        {
                            "rule": "formula_recalculation_mismatch",
                            "observed": value,
                            "recalculated": recalculated,
                        }
                    )
                recompute_status = "recomputed"
                trust_level = "recalculated"
            except (ValueError, TypeError, ZeroDivisionError, statistics.StatisticsError):
                recompute_status = "inputs_missing" if contract == "strict_formula_recalculation" else "not_applicable"
                trust_level = "cited_not_recalculated" if contract == "strict_formula_recalculation" else "display_only"

        else:
            # expression/aggregate/statistical 没拿到对应输入，或 constant/display/
            # qualitative：如实反映"没有求值器可用"，不冒充任何一种已核实状态。
            recompute_status = "not_applicable"
            trust_level = {
                "strict_formula_recalculation": "cited_not_recalculated",
                "citation_only": "cited_not_recalculated",
                "display_value_only": "display_only",
                "non_numeric_design_status": "qualitative",
            }.get(contract, "display_only")

        # 2026-07-02 止血修正：诚实失败的"报警"职责放在 fact_verification_service
        # （真正的验证网关，见 §6/§9），不放在 normalize() 里。原因：normalize() 是被
        # project_field_discovery_service / project_file_parser_service 等多处共用的
        # 通用工具，这些调用方对同一个 strict 指标常常本来就不传 numerator/denominator
        # （比如纯展示值列匹配、探索性字段猜测的失败判定路径），如果在这里把
        # "没传分子分母"直接变成 valid=False，会连带把这些无关调用方的正常路径判失败，
        # 造成新的静默丢证据回归——这正是本方案要消灭的问题，不能在修一个坑时开
        # 另一个坑。因此这里只如实返回 recompute_status/trust_level 供调用方按需判断，
        # 是否把"该算却没算成"升级为 issue，由真正做验证判定的调用方（fact_verification_
        # service）决定。valid/issues 在 inputs_missing 场景下保持改造前的行为不变。

        valid_range = schema.get("valid_range") or [None, None]
        lower = valid_range[0] if len(valid_range) > 0 else None
        upper = valid_range[1] if len(valid_range) > 1 else None
        if (lower is not None and value < lower) or (upper is not None and value > upper):
            issues.append(
                {
                    "rule": "metric_value_out_of_physical_range",
                    "value": value,
                    "valid_range": valid_range,
                }
            )

        return {
            "metric_id": canonical,
            "value": value,
            "display_value": cls.format_value(canonical, value),
            "input_scale": input_scale,
            "value_scale": expected_scale,
            "conversion": conversion,
            "valid": not issues,
            "issues": issues,
            "schema": schema,
            "recompute_status": recompute_status,
            "trust_level": trust_level,
        }

    @classmethod
    def format_value(cls, metric_id: Any, value: Any) -> str:
        schema = cls.get(metric_id)
        number = cls._number(value)
        if number is None:
            return "-"
        scale = str(schema.get("value_scale") or "number")
        display_unit = str(schema.get("display_unit") or "")
        if scale == "fraction" and display_unit == "%":
            return f"{number * 100:.2f}%"
        if scale == "percent":
            return f"{number:.2f}%"
        if scale == "count":
            return str(int(round(number)))
        return f"{number:.4f}".rstrip("0").rstrip(".")

    @classmethod
    def _input_scale(
        cls,
        raw_value: Any,
        source_field: str,
        schema: dict[str, Any],
    ) -> str:
        raw_text = str(raw_value or "").strip().lower()
        field = str(source_field or "").strip().lower()
        if "percent_duplication" in field:
            return "fraction"
        if "%" in raw_text or "%" in field or "percent" in field:
            return "percent"
        if any(token in field for token in ("rate",)) and str(schema.get("value_scale") or "") == "percent":
            return "percent"
        if any(token in field for token in ("fraction", "proportion")):
            return "fraction"
        return str(schema.get("source_scale") or schema.get("value_scale") or "number")

    @staticmethod
    def _number(value: Any) -> float | None:
        if value in (None, "") or isinstance(value, bool):
            return None
        text = str(value).strip()
        if "(" in text and "%)" in text:
            text = text.rsplit("(", 1)[1].split("%", 1)[0]
        text = text.replace(",", "").rstrip("%").strip()
        try:
            parsed = float(text)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    # ── Stage 4（truth_layer_recompute_generalization_plan.md §8 末 / §11 step4）──
    # 护栏：任何指标缺 recompute，或 contract 比该 recompute 方法"配得上"的最高信任
    # 等级还高（比如 statistical/constant/display 却敢声明 strict_formula_recalculation，
    # 就是 §1.3 那个 bug 本身），一律判为不一致。同一 recompute 方法允许声明比上限更保守
    # 的 contract（比如 ratio 型指标数据经常不全，主动降级成 citation_only 是合理的，
    # 不算违规）——这里守住的是"绝不能名不副实地升级"，不是"必须用默认档"。
    _CONTRACT_RANK = {
        "strict_formula_recalculation": 3,
        "citation_only": 2,
        "display_value_only": 1,
        "non_numeric_design_status": 0,
    }
    _MAX_CONTRACT_RANK_BY_RECOMPUTE = {
        "ratio": 3,
        "expression": 3,
        "aggregate": 3,
        "statistical": 2,
        "constant": 2,
        "display": 1,
        "qualitative": 0,
    }

    @classmethod
    def validate_registry(cls, *, metrics: dict[str, dict[str, Any]] | None = None) -> list[str]:
        """检查每条指标是否显式声明 recompute，且 contract 没有超过该 recompute
        方法配得上的最高信任等级。返回问题描述列表；不为空即代表注册表有指标又掉进
        "默认/名不副实"陷阱。默认检查 `METRICS`（人工维护基线）；传 `metrics` 可以
        检查运行时注册表（含 `register_metric()` 追加的候选指标）。
        """
        problems: list[str] = []
        source = metrics if metrics is not None else cls.METRICS
        for metric_id, schema in source.items():
            recompute_method = schema.get("recompute")
            if not recompute_method:
                problems.append(f"{metric_id}: 缺少显式 recompute 声明")
                continue
            recompute_method = str(recompute_method)
            if recompute_method not in cls._MAX_CONTRACT_RANK_BY_RECOMPUTE:
                problems.append(f"{metric_id}: recompute={recompute_method!r} 不在白名单内")
                continue
            contract = str(schema.get("verifier_contract") or "")
            if contract not in cls._CONTRACT_RANK:
                problems.append(f"{metric_id}: verifier_contract={contract!r} 不是已知 contract")
                continue
            max_rank = cls._MAX_CONTRACT_RANK_BY_RECOMPUTE[recompute_method]
            if cls._CONTRACT_RANK[contract] > max_rank:
                problems.append(
                    f"{metric_id}: recompute={recompute_method!r} 最高只配得上 "
                    f"rank<={max_rank}，但声明了 verifier_contract={contract!r}"
                    f"（rank={cls._CONTRACT_RANK[contract]}）——名不副实"
                )
        return problems

    @classmethod
    def validate_registry_or_raise(cls, *, metrics: dict[str, dict[str, Any]] | None = None) -> None:
        problems = cls.validate_registry(metrics=metrics)
        if problems:
            raise AssertionError(
                "metric_schema_service 注册表一致性检查失败，共 "
                f"{len(problems)} 条：\n" + "\n".join(f"  - {p}" for p in problems)
            )


MetricSchemaService._apply_phase0_defaults()
# Stage 4 护栏：只校验 METRICS 这份人工维护基线（启动即全量已知，可以安全 fail-fast）。
# 运行时通过 register_metric() 追加的候选指标走另一条生命周期（脚本公式转正 /
# 候选指标审核），那些路径自己负责补全 recompute+contract，不在这里做硬性阻断，
# 避免一次异常写入就拖垮整个进程的候选指标功能。
MetricSchemaService.validate_registry_or_raise()

metric_schema_service = MetricSchemaService()
