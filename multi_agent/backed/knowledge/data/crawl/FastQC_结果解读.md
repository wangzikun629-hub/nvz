# FastQC 结果解读知识库

- [1. FastQC 结果整体判定规则](#1-fastqc-结果整体判定规则)
- [2. Basic Statistics（基本统计信息）](#2-basic-statistics基本统计信息)
- [3. 序列测序质量统计](#3-序列测序质量统计)
- [4. Tile 的测序质量](#4-tile-的测序质量)
- [5. 序列的测序质量](#5-序列的测序质量)
- [6. 序列碱基含量](#6-序列碱基含量)
- [7. GC 含量统计](#7-gc-含量统计)
- [8. reads 每个位置 N 的比率统计](#8-reads-每个位置-n-的比率统计)
- [9. reads 的长度分布](#9-reads-的长度分布)
- [10. 重复 reads 的次数统计](#10-重复-reads-的次数统计)
- [11. 过多的重复序列](#11-过多的重复序列)
- [12. 接头含量](#12-接头含量)

## 1. FastQC 结果整体判定规则

**本节问题列表**

- [FQ-001：FastQC 结果报告的结果等级分为哪几种？](#fq-001-fastqc-结果报告的结果等级分为哪几种)

### FQ-001：FastQC 结果报告的结果等级分为哪几种？

**核心答案**

FastQC 结果分为 3 种等级，绿色代表 PASS；黄色代表 WARN；红色代表 FAIL。当出现黄色结果时，需要查看对应结果详情。

---

## 2. Basic Statistics（基本统计信息）

**本节问题列表**

- [FQ-002：Basic Statistics 模块中 Filename 字段代表什么？](#fq-002-basic-statistics-模块中-filename-字段代表什么)
- [FQ-003：Basic Statistics 模块中 File type 字段代表什么？](#fq-003-basic-statistics-模块中-file-type-字段代表什么)
- [FQ-004：Basic Statistics 模块中 Encoding 字段代表什么？](#fq-004-basic-statistics-模块中-encoding-字段代表什么)
- [FQ-005：Basic Statistics 模块中 Total Sequences 字段代表什么？](#fq-005-basic-statistics-模块中-total-sequences-字段代表什么)
- [FQ-006：Basic Statistics 模块中 Sequences flagged as poor quality 字段代表什么？](#fq-006-basic-statistics-模块中-sequences-flagged-as-poor-quality-字段代表什么)
- [FQ-007：Basic Statistics 模块中 Sequence length 字段代表什么？](#fq-007-basic-statistics-模块中-sequence-length-字段代表什么)
- [FQ-008：Basic Statistics 模块中 % GC 字段代表什么？](#fq-008-basic-statistics-模块中--gc-字段代表什么)

### FQ-002：Basic Statistics 模块中 Filename 字段代表什么？

**核心答案**

Filename 代表测序文件的文件名。

---

### FQ-003：Basic Statistics 模块中 File type 字段代表什么？

**核心答案**

File type 代表测序文件的文件类型。

---

### FQ-004：Basic Statistics 模块中 Encoding 字段代表什么？

**核心答案**

Encoding 代表测序平台的版本和相应的编码版本号。

---

### FQ-005：Basic Statistics 模块中 Total Sequences 字段代表什么？

**核心答案**

Total Sequences 代表 total reads 的数量，该数值不包含 Sequences flagged as poor quality 标记的序列。

---

### FQ-006：Basic Statistics 模块中 Sequences flagged as poor quality 字段代表什么？

**核心答案**

在 Casava 模式运行时，需要被过滤的序列会被该字段标记，这些序列不会参加 QC 分析，也不计入 Total Sequences 的统计数值中。

---

### FQ-007：Basic Statistics 模块中 Sequence length 字段代表什么？

**核心答案**

Sequence length 代表测序长度。

---

### FQ-008：Basic Statistics 模块中 % GC 字段代表什么？

**核心答案**

% GC 代表整体序列的 GC 含量，二代测序的 GC 偏好性高，且测序深度越高，GC 含量会越高。

---

## 3. 序列测序质量统计

**本节问题列表**

- [FQ-009：Per base sequence quality 模块的横轴和纵轴分别代表什么？](#fq-009-per-base-sequence-quality-模块的横轴和纵轴分别代表什么)
- [FQ-010：Per base sequence quality 模块中的各线条分别代表什么？](#fq-010-per-base-sequence-quality-模块中的各线条分别代表什么)
- [FQ-011：碱基质量分数的核心意义是什么？](#fq-011-碱基质量分数的核心意义是什么)
- [FQ-012：Per base sequence quality 模块的 WARN 判定阈值是什么？](#fq-012-per-base-sequence-quality-模块的-warn-判定阈值是什么)
- [FQ-013：Per base sequence quality 模块的 FAIL 判定阈值是什么？](#fq-013-per-base-sequence-quality-模块的-fail-判定阈值是什么)

### FQ-009：Per base sequence quality 模块的横轴和纵轴分别代表什么？

**核心答案**

横轴是测序序列的第一个碱基到第 N 个碱基（read 长度）；纵轴是碱基质量得分。

---

### FQ-010：Per base sequence quality 模块中的各线条分别代表什么？

**核心答案**

红线表示碱基质量的中位数，黄色区域是 25%-75% 质量值区间，误差线是 10%-90% 质量值区间，蓝线是质量值的平均值。

---

### FQ-011：碱基质量分数的核心意义是什么？

**核心答案**

碱基质量分数与错误率是衡量测序质量的重要指标，质量值越高代表碱基被测错的概率越小。

---

### FQ-012：Per base sequence quality 模块的 WARN 判定阈值是什么？

**核心答案**

任何碱基质量低于 10，或是任何位置的碱基质量中位数低于 25，报 “WARN”。

---

### FQ-013：Per base sequence quality 模块的 FAIL 判定阈值是什么？

**核心答案**

任何碱基质量低于 5，或是任何位置的碱基质量中位数低于 20，报 “FAIL”。

---

## 4. Tile 的测序质量

**本节问题列表**

- [FQ-014：Per tile sequence quality 模块的核心作用是什么？](#fq-014-per-tile-sequence-quality-模块的核心作用是什么)
- [FQ-015：Per tile sequence quality 模块的横轴和纵轴分别代表什么？](#fq-015-per-tile-sequence-quality-模块的横轴和纵轴分别代表什么)
- [FQ-016：Per tile sequence quality 模块中不同颜色代表什么含义？](#fq-016-per-tile-sequence-quality-模块中不同颜色代表什么含义)
- [FQ-017：Per tile sequence quality 模块的 WARN 判定阈值是什么？](#fq-017-per-tile-sequence-quality-模块的-warn-判定阈值是什么)
- [FQ-018：Per tile sequence quality 模块的 FAIL 判定阈值是什么？](#fq-018-per-tile-sequence-quality-模块的-fail-判定阈值是什么)
- [FQ-019：华大下机数据是否包含 Per tile sequence quality 模块的结果？](#fq-019-华大下机数据是否包含-per-tile-sequence-quality-模块的结果)
- [FQ-020：Per tile sequence quality 显示 fail 或者 warning 代表什么？](#fq-020-per-tile-sequence-quality-显示-fail-或者-warning-代表什么)
- [FQ-021：Per tile sequence quality 中不同位置和 cycles 随机出现低质量暖色调区域的原因是什么？](#fq-021-per-tile-sequence-quality-中不同位置和-cycles-随机出现低质量暖色调区域的原因是什么)
- [FQ-022：Per tile sequence quality 中 flow cell 上有四个区域出现连续的低质量暖色条的原因是什么？](#fq-022-per-tile-sequence-quality-中-flow-cell-上有四个区域出现连续的低质量暖色条的原因是什么)
- [FQ-023：Per tile sequence quality 中低质量区域在测序开始时没有，在运行中出现并持续到结束的原因是什么？](#fq-023-per-tile-sequence-quality-中低质量区域在测序开始时没有在运行中出现并持续到结束的原因是什么)
- [FQ-024：Per tile sequence quality 中特定区域出现暂时性质量下降的原因是什么？](#fq-024-per-tile-sequence-quality-中特定区域出现暂时性质量下降的原因是什么)
- [FQ-025：flowcell 中的气泡会造成哪些测序影响？](#fq-025-flowcell-中的气泡会造成哪些测序影响)

### FQ-014：Per tile sequence quality 模块的核心作用是什么？

**核心答案**

展示每个 tile 的质量值情况，可通过该模块识别因 flow cell 或测序 run 的故障造成的测序错误。

---

### FQ-015：Per tile sequence quality 模块的横轴和纵轴分别代表什么？

**核心答案**

横轴表示序列的碱基位置；纵轴表示 tile 的 Index 编号。

---

### FQ-016：Per tile sequence quality 模块中不同颜色代表什么含义？

**核心答案**

蓝色表示测序质量平均，暖色表示测序质量相比平均质量值低。

---

### FQ-017：Per tile sequence quality 模块的 WARN 判定阈值是什么？

**核心答案**

如果任何 tile 显示的平均 Phred 分数比所有 tile 中该碱基的平均值低 2 分，报 “WARN”。

---

### FQ-018：Per tile sequence quality 模块的 FAIL 判定阈值是什么？

**核心答案**

如果任何 tile 显示的平均 Phred 分数比所有 tile 中该碱基的平均值低 5 分，报 “FAIL”。

---

### FQ-019：华大下机数据是否包含 Per tile sequence quality 模块的结果？

**核心答案**

华大下机数据无该模块结果。

---

### FQ-020：Per tile sequence quality 显示 fail 或者 warning 代表什么？

**核心答案**

表明测序的 lane 或某个 run 中出现了部分故障，从而影响一些特定的区域和循环，进而使测序数据的质量下降。

---

### FQ-021：Per tile sequence quality 中不同位置和 cycles 随机出现低质量暖色调区域的原因是什么？

**核心答案**

flow cell 过载。

---

### FQ-022：Per tile sequence quality 中 flow cell 上有四个区域出现连续的低质量暖色条的原因是什么？

**核心答案**

测序 run 的总体质量略低，而 flowcell 并没有过载，一般是由于测序的序列有偏差造成的；高亮区域为 flow cell 的边缘，拍照系统识别 read 信号的能力下降，该类数据通常仍可使用。

---

### FQ-023：Per tile sequence quality 中低质量区域在测序开始时没有，在运行中出现并持续到结束的原因是什么？

**核心答案**

拍照系统受到阻挡，比如有脏东西掉在 flowcell 的表面，或者一些东西被冲进了 flowcell 内并卡在其中；该类阻塞现象通常成对出现，来自这些区域的序列通常在质控中能被修剪移除。

---

### FQ-024：Per tile sequence quality 中特定区域出现暂时性质量下降的原因是什么？

**核心答案**

有异物被冲进了 flowcell 中，阻塞了部分测序循环，最后又被冲洗出去；该问题主要由 flowcell 中的气泡引起。

---

### FQ-025：flowcell 中的气泡会造成哪些测序影响？

**核心答案**

1. 气泡阻止拍照系统正确拍照，还使测序试剂无法流入 flowcell 的纳米孔中，进而无法形成 cluster，导致气泡下的 cluster 跳过了 sequencing chemistry cycles；2. 使得气泡被引入之前的最后一个碱基被重复读取，最终导致序列被人为的延伸，即引入了插入片段；3. 若相关 reads 用于检测 SNP，假的插入片段将会混淆对下游分析结果的解释。

---

## 5. 序列的测序质量

**本节问题列表**

- [FQ-026：Per sequence quality scores 模块的核心作用是什么？](#fq-026-per-sequence-quality-scores-模块的核心作用是什么)
- [FQ-027：Per sequence quality scores 模块的横轴和纵轴分别代表什么？](#fq-027-per-sequence-quality-scores-模块的横轴和纵轴分别代表什么)
- [FQ-028：Per sequence quality scores 模块中，低质量坐标位置出现额外峰代表什么？](#fq-028-per-sequence-quality-scores-模块中低质量坐标位置出现额外峰代表什么)
- [FQ-029：Per sequence quality scores 模块的 WARN 判定阈值是什么？](#fq-029-per-sequence-quality-scores-模块的-warn-判定阈值是什么)
- [FQ-030：Per sequence quality scores 模块的 FAIL 判定阈值是什么？](#fq-030-per-sequence-quality-scores-模块的-fail-判定阈值是什么)
- [FQ-031：Per sequence quality scores 中，小部分序列在低质量区形成小峰的原因是什么？](#fq-031-per-sequence-quality-scores-中小部分序列在低质量区形成小峰的原因是什么)
- [FQ-032：Per sequence quality scores 中，一次 run 的很多数据在低质量区形成峰的原因是什么？](#fq-032-per-sequence-quality-scores-中一次-run-的很多数据在低质量区形成峰的原因是什么)

### FQ-026：Per sequence quality scores 模块的核心作用是什么？

**核心答案**

用来查看碱基质量是否存在普遍过低的情况。

---

### FQ-027：Per sequence quality scores 模块的横轴和纵轴分别代表什么？

**核心答案**

横轴为序列平均碱基质量值，纵坐标为序列的数量。

---

### FQ-028：Per sequence quality scores 模块中，低质量坐标位置出现额外峰代表什么？

**核心答案**

说明测序数据中有一部分序列质量较差。

---

### FQ-029：Per sequence quality scores 模块的 WARN 判定阈值是什么？

**核心答案**

当序列平均碱基质量峰值小于 27（错误率 0.2%）时报 “WARN”。

---

### FQ-030：Per sequence quality scores 模块的 FAIL 判定阈值是什么？

**核心答案**

当序列平均碱基质量峰值小于 20（错误率 1%）时报 “FAIL”。

---

### FQ-031：Per sequence quality scores 中，小部分序列在低质量区形成小峰的原因是什么？

**核心答案**

可能由于小区域的荧光成像质量较差，例如在 tile 的边缘。

---

### FQ-032：Per sequence quality scores 中，一次 run 的很多数据在低质量区形成峰的原因是什么？

**核心答案**

可能是测序出现某些系统性的错误，也有可能只是该次 run 的一部分有问题，例如在 flowcell 某一末端。

---

## 6. 序列碱基含量

**本节问题列表**

- [FQ-033：Per base sequence content 模块中，DNA-Seq 的正常碱基含量特征是什么？](#fq-033-per-base-sequence-content-模块中dna-seq-的正常碱基含量特征是什么)
- [FQ-034：Per base sequence content 模块中，数据随机性差的表现是什么？](#fq-034-per-base-sequence-content-模块中数据随机性差的表现是什么)
- [FQ-035：Per base sequence content 模块中，哺乳动物典型 BS-Seq 实验的碱基含量特征是什么？](#fq-035-per-base-sequence-content-模块中哺乳动物典型-bs-seq-实验的碱基含量特征是什么)
- [FQ-036：Per base sequence content 模块中，RNA-Seq 的碱基含量特征是什么？](#fq-036-per-base-sequence-content-模块中rna-seq-的碱基含量特征是什么)
- [FQ-037：Per base sequence content 模块中，Small RNA 文库的碱基含量特征是什么？](#fq-037-per-base-sequence-content-模块中small-rna-文库的碱基含量特征是什么)
- [FQ-038：Per base sequence content 模块中，扩增子文库的碱基含量特征是什么？](#fq-038-per-base-sequence-content-模块中扩增子文库的碱基含量特征是什么)
- [FQ-039：Per base sequence content 模块中，reads 开头出现碱基组成偏离的原因是什么？](#fq-039-per-base-sequence-content-模块中reads-开头出现碱基组成偏离的原因是什么)
- [FQ-040：Per base sequence content 模块中，reads 结尾出现碱基组成偏离的原因是什么？](#fq-040-per-base-sequence-content-模块中reads-结尾出现碱基组成偏离的原因是什么)
- [FQ-041：Per base sequence content 模块中，所有位置的碱基比例一致出现偏差（四条线平行且分开）代表什么？](#fq-041-per-base-sequence-content-模块中所有位置的碱基比例一致出现偏差四条线平行且分开代表什么)
- [FQ-042：Per base sequence content 模块的 WARN 判定阈值是什么？](#fq-042-per-base-sequence-content-模块的-warn-判定阈值是什么)
- [FQ-043：Per base sequence content 模块的 FAIL 判定阈值是什么？](#fq-043-per-base-sequence-content-模块的-fail-判定阈值是什么)

### FQ-033：Per base sequence content 模块中，DNA-Seq 的正常碱基含量特征是什么？

**核心答案**

A 和 T 应该相等，G 和 C 应该相等，四条线应该平行，一般前几个碱基会出现波动。

---

### FQ-034：Per base sequence content 模块中，数据随机性差的表现是什么？

**核心答案**

4 条线交错分布，部分位置碱基的比例出现 bias，四条线在某些位置纷乱交织，往往提示有某个序列大量出现的污染。

---

### FQ-035：Per base sequence content 模块中，哺乳动物典型 BS-Seq 实验的碱基含量特征是什么？

**核心答案**

整个序列全长的 C 含量保持在 1~2%，根据细胞类型或物种的甲基化程度不同，这个比例可能会有变化。

---

### FQ-036：Per base sequence content 模块中，RNA-Seq 的碱基含量特征是什么？

**核心答案**

大多数 RNA-Seq 文库会出现前 10~15 个碱基明显分布不均匀，这是因为反转录成 cDNA 时所用的随机引物会引起核苷酸组成存在一定的偏好性；其他碱基位置的 A 和 T 应该相等，G 和 C 应该相等，四条线应该平行。

---

### FQ-037：Per base sequence content 模块中，Small RNA 文库的碱基含量特征是什么？

**核心答案**

sRNA 序列种类相对有限，DNA 分子多样性差，且 miRNA 通常有过表达现象，因此四种碱基含量不稳定，四条线交错分布，这是文库本身特征导致的，属于正常现象。

---

### FQ-038：Per base sequence content 模块中，扩增子文库的碱基含量特征是什么？

**核心答案**

文库复杂度低，DNA 分子多样性差，因此四种碱基含量不稳定，四条线交错分布，这是文库本身特征导致的，属于正常现象。

---

### FQ-039：Per base sequence content 模块中，reads 开头出现碱基组成偏离的原因是什么？

**核心答案**

由建库操作造成的，比如建 GBS 文库时在 reads 开头加了 barcode，barcode 的碱基组成不是均一的，酶切位点的碱基组成是固定不变的，会造成明显的碱基组成偏离。

---

### FQ-040：Per base sequence content 模块中，reads 结尾出现碱基组成偏离的原因是什么？

**核心答案**

由测序接头的污染造成的。

---

### FQ-041：Per base sequence content 模块中，所有位置的碱基比例一致出现偏差（四条线平行且分开）代表什么？

**核心答案**

代表文库有偏差，或测序中的系统误差。

---

### FQ-042：Per base sequence content 模块的 WARN 判定阈值是什么？

**核心答案**

当任一位置的 A/T 比例与 G/C 比例相差超过 10%，报 “WARN”。

---

### FQ-043：Per base sequence content 模块的 FAIL 判定阈值是什么？

**核心答案**

当任一位置的 A/T 比例与 G/C 比例相差超过 20%，报 “FAIL”。

---

## 7. GC 含量统计

**本节问题列表**

- [FQ-044：Per base GC content 模块的核心作用是什么？](#fq-044-per-base-gc-content-模块的核心作用是什么)
- [FQ-045：Per base GC content 模块中，蓝线和红线分别代表什么？](#fq-045-per-base-gc-content-模块中蓝线和红线分别代表什么)
- [FQ-046：Per base GC content 模块中，建库均匀的样品的 GC 含量特征是什么？](#fq-046-per-base-gc-content-模块中建库均匀的样品的-gc-含量特征是什么)
- [FQ-047：Per base GC content 模块中，部分位置 GC 含量出现偏差代表什么？](#fq-047-per-base-gc-content-模块中部分位置-gc-含量出现偏差代表什么)
- [FQ-048：Per base GC content 模块中，所有位置 GC 含量一致出现偏差代表什么？](#fq-048-per-base-gc-content-模块中所有位置-gc-含量一致出现偏差代表什么)
- [FQ-049：人全基因组的 GC 含量正常范围是多少？](#fq-049-人全基因组的-gc-含量正常范围是多少)
- [FQ-050：人外显子区域的 GC 含量正常范围是多少？](#fq-050-人外显子区域的-gc-含量正常范围是多少)
- [FQ-051：酿酒酵母菌和结核分枝杆菌的 GC 含量正常范围是多少？](#fq-051-酿酒酵母菌和结核分枝杆菌的-gc-含量正常范围是多少)
- [FQ-052：哺乳动物 BS-Seq 文库的 GC 含量峰值正常范围是多少？](#fq-052-哺乳动物-bs-seq-文库的-gc-含量峰值正常范围是多少)
- [FQ-053：RNA-Seq 文库的 GC 含量特征是什么？](#fq-053-rna-seq-文库的-gc-含量特征是什么)
- [FQ-054：Small RNA 文库的 GC 含量特征是什么？](#fq-054-small-rna-文库的-gc-含量特征是什么)
- [FQ-055：扩增子文库的 GC 含量特征是什么？](#fq-055-扩增子文库的-gc-含量特征是什么)
- [FQ-056：Per base GC content 模块的 WARN 判定阈值是什么？](#fq-056-per-base-gc-content-模块的-warn-判定阈值是什么)
- [FQ-057：Per base GC content 模块的 FAIL 判定阈值是什么？](#fq-057-per-base-gc-content-模块的-fail-判定阈值是什么)

### FQ-044：Per base GC content 模块的核心作用是什么？

**核心答案**

对所有 reads 的每个位置统计 GC 含量，反映样品的 GC 含量情况。

---

### FQ-045：Per base GC content 模块中，蓝线和红线分别代表什么？

**核心答案**

蓝线为系统计算得到的理论分布；红线为测量值，二者越接近越好。

---

### FQ-046：Per base GC content 模块中，建库均匀的样品的 GC 含量特征是什么？

**核心答案**

如果建库足够均匀，reads 的每个位置应当是没有差异的，GC 含量的线应当平行于 X 轴。

---

### FQ-047：Per base GC content 模块中，部分位置 GC 含量出现偏差代表什么？

**核心答案**

往往提示样品存在污染。

---

### FQ-048：Per base GC content 模块中，所有位置 GC 含量一致出现偏差代表什么？

**核心答案**

往往表示文库有偏差或是测序中的系统误差；如果形状正常但出现平移，表示出现的系统误差与碱基位置无关，也有可能是理论曲线不能反映当前测序的基因组的平均 GC 含量，属于正常结果。

---

### FQ-049：人全基因组的 GC 含量正常范围是多少？

**核心答案**

人全基因组的 GC 含量一般在 38~39%。

---

### FQ-050：人外显子区域的 GC 含量正常范围是多少？

**核心答案**

人外显子区域的 GC 含量一般在 49~51%。

---

### FQ-051：酿酒酵母菌和结核分枝杆菌的 GC 含量正常范围是多少？

**核心答案**

酿酒酵母菌和结核分枝杆菌的 GC 含量一般在 38~42%。

---

### FQ-052：哺乳动物 BS-Seq 文库的 GC 含量峰值正常范围是多少？

**核心答案**

一般哺乳动物的 BS-Seq 文库的 GC 含量峰值在 20~30% 之间。

---

### FQ-053：RNA-Seq 文库的 GC 含量特征是什么？

**核心答案**

RNA-Seq 中由于转录本的平均 CG 含量差异，会造成实际分布比理论分布宽或窄；Total RNA-seq 文库的 GC 含量一般在 39.7%(IncRNA) - 48.9%(coding RNA) 之间。

---

### FQ-054：Small RNA 文库的 GC 含量特征是什么？

**核心答案**

Small RNA 文库的实际数据分布很窄，这是文库本身特征导致的，属于正常现象。

---

### FQ-055：扩增子文库的 GC 含量特征是什么？

**核心答案**

扩增子文库的实际数据分布很窄，这是文库本身特征导致的，属于正常现象。

---

### FQ-056：Per base GC content 模块的 WARN 判定阈值是什么？

**核心答案**

当任一位置的 GC 含量偏离均值的 5% 时，报 “WARN”。

---

### FQ-057：Per base GC content 模块的 FAIL 判定阈值是什么？

**核心答案**

当任一位置的 GC 含量偏离均值的 10% 时，报 “FAIL”。

---

## 8. reads 每个位置 N 的比率统计

**本节问题列表**

- [FQ-058：测序数据中 N 碱基产生的原因是什么？](#fq-058-测序数据中-n-碱基产生的原因是什么)
- [FQ-059：Per base N content 模块的正常结果特征是什么？](#fq-059-per-base-n-content-模块的正常结果特征是什么)
- [FQ-060：Per base N content 模块中，Y 轴 0%-100% 范围内出现 “鼓包” 代表什么？](#fq-060-per-base-n-content-模块中y-轴-0-100-范围内出现-鼓包-代表什么)
- [FQ-061：Per base N content 模块的 WARN 判定阈值是什么？](#fq-061-per-base-n-content-模块的-warn-判定阈值是什么)
- [FQ-062：Per base N content 模块的 FAIL 判定阈值是什么？](#fq-062-per-base-n-content-模块的-fail-判定阈值是什么)

### FQ-058：测序数据中 N 碱基产生的原因是什么？

**核心答案**

当测序仪器不能辨别某条 reads 的某个位置是 ATCG 哪个碱基时，就会产生 'N'。

---

### FQ-059：Per base N content 模块的正常结果特征是什么？

**核心答案**

正常情况下 N 的比例是很小的，图上常看到一条直线，放大 Y 轴之后发现的少量 N 不算问题。

---

### FQ-060：Per base N content 模块中，Y 轴 0%-100% 范围内出现 “鼓包” 代表什么？

**核心答案**

说明测序系统出了问题。

---

### FQ-061：Per base N content 模块的 WARN 判定阈值是什么？

**核心答案**

当任意位置的 N 的比例超过 5% 报 “WARN”。

---

### FQ-062：Per base N content 模块的 FAIL 判定阈值是什么？

**核心答案**

当任意位置的 N 的比例超过 20%，报 “FAIL”。

---

## 9. reads 的长度分布

**本节问题列表**

- [FQ-063：Sequence Length Distribution 模块的横轴和纵轴分别代表什么？](#fq-063-sequence-length-distribution-模块的横轴和纵轴分别代表什么)
- [FQ-064：测序仪原始下机数据的 reads 长度分布正常特征是什么？](#fq-064-测序仪原始下机数据的-reads-长度分布正常特征是什么)
- [FQ-065：Sequence Length Distribution 中出现多个峰代表什么？](#fq-065-sequence-length-distribution-中出现多个峰代表什么)
- [FQ-066：Sequence Length Distribution 模块的 WARN 判定阈值是什么？](#fq-066-sequence-length-distribution-模块的-warn-判定阈值是什么)
- [FQ-067：Sequence Length Distribution 模块的 FAIL 判定阈值是什么？](#fq-067-sequence-length-distribution-模块的-fail-判定阈值是什么)

### FQ-063：Sequence Length Distribution 模块的横轴和纵轴分别代表什么？

**核心答案**

横轴为序列长度，纵轴为序列数量。

---

### FQ-064：测序仪原始下机数据的 reads 长度分布正常特征是什么？

**核心答案**

测序仪产生的原始数据应该全部序列长度一致。

---

### FQ-065：Sequence Length Distribution 中出现多个峰代表什么？

**核心答案**

对于测序仪原始下机数据，出现多个峰表明测序结果不可信；如果是经过 trimming 的过滤后数据或特殊的测序平台，不同长度的 Reads 造成多个峰是合理的。

---

### FQ-066：Sequence Length Distribution 模块的 WARN 判定阈值是什么？

**核心答案**

当 reads 长度不一致时报 “WARN”。

---

### FQ-067：Sequence Length Distribution 模块的 FAIL 判定阈值是什么？

**核心答案**

当长度为 0 的 reads 时报 “FAIL”。

---

## 10. 重复 reads 的次数统计

**本节问题列表**

- [FQ-068：Sequence Duplication Levels 模块的核心作用是什么？](#fq-068-sequence-duplication-levels-模块的核心作用是什么)
- [FQ-069：Sequence Duplication Levels 模块的统计规则是什么？](#fq-069-sequence-duplication-levels-模块的统计规则是什么)
- [FQ-070：Sequence Duplication Levels 中重复程度很高代表什么？](#fq-070-sequence-duplication-levels-中重复程度很高代表什么)
- [FQ-071：RNA-Seq 文库出现整体 Duplication 是否正常？](#fq-071-rna-seq-文库出现整体-duplication-是否正常)
- [FQ-072：Sequence Duplication Levels 模块的 WARN 判定阈值是什么？](#fq-072-sequence-duplication-levels-模块的-warn-判定阈值是什么)
- [FQ-073：Sequence Duplication Levels 模块的 FAIL 判定阈值是什么？](#fq-073-sequence-duplication-levels-模块的-fail-判定阈值是什么)

### FQ-068：Sequence Duplication Levels 模块的核心作用是什么？

**核心答案**

统计完全一样 reads 的频率，横坐标是 duplication 的次数，纵坐标是 duplicated reads 的数目，以 unique reads 的总数作为 100%。

---

### FQ-069：Sequence Duplication Levels 模块的统计规则是什么？

**核心答案**

Fastqc 中用测序数据的前 200000 条 reads 统计其在全部数据中的重复情况。

---

### FQ-070：Sequence Duplication Levels 中重复程度很高代表什么？

**核心答案**

测序深度越高，越容易产生一定程度的重复，但重复程度很高，可能是有偏差的存在。

---

### FQ-071：RNA-Seq 文库出现整体 Duplication 是否正常？

**核心答案**

对于 RNA-Seq 文库来说，由于可能真实存在一些转录本过表达的情况，且为了观察到低表达的转录本，往往是过量测序的，因此可能会出现一些整体的 Duplication，这是合理的，一般在比对步骤去除 Duplication。

---

### FQ-072：Sequence Duplication Levels 模块的 WARN 判定阈值是什么？

**核心答案**

当非 unique 的 reads 占总数的比例 > 20% 时报 “WARN”。

---

### FQ-073：Sequence Duplication Levels 模块的 FAIL 判定阈值是什么？

**核心答案**

当非 unique 的 reads 占总数的比例 > 50% 时报 “FAIL”。

---

## 11. 过多的重复序列

**本节问题列表**

- [FQ-074：什么是 Overrepresented sequences？](#fq-074-什么是-overrepresented-sequences)
- [FQ-075：Overrepresented sequences 模块的统计规则是什么？](#fq-075-overrepresented-sequences-模块的统计规则是什么)
- [FQ-076：Overrepresented sequences 模块中，如何判定序列的污染来源？](#fq-076-overrepresented-sequences-模块中如何判定序列的污染来源)
- [FQ-077：Overrepresented sequences 模块的 WARN 判定阈值是什么？](#fq-077-overrepresented-sequences-模块的-warn-判定阈值是什么)
- [FQ-078：Overrepresented sequences 模块的 FAIL 判定阈值是什么？](#fq-078-overrepresented-sequences-模块的-fail-判定阈值是什么)

### FQ-074：什么是 Overrepresented sequences？

**核心答案**

如果有某个序列大量出现，就叫做 over-represented，Fastqc 的判定标准是该序列占全部 reads 的 0.1% 以上。

---

### FQ-075：Overrepresented sequences 模块的统计规则是什么？

**核心答案**

为计算方便只取测序数据前 200000 条 reads 进行统计，有可能 over-represented reads 不在里面；大于 75bp 的 reads 也是只取 50bp。

---

### FQ-076：Overrepresented sequences 模块中，如何判定序列的污染来源？

**核心答案**

若在运行时加入 - c contaminant file，出现的 over-represented sequence 会从 contaminant file 里面找匹配的 hit（至少 20bp 且最多一个 mismatch）；每条 Overrepresented 序列会和污染物（接头和引物）比对，至少 20bp 比对上且错配不大于 1 的认为是可能的污染来源。

---

### FQ-077：Overrepresented sequences 模块的 WARN 判定阈值是什么？

**核心答案**

发现超过总数 0.1% 的 reads 报 “WARN”。

---

### FQ-078：Overrepresented sequences 模块的 FAIL 判定阈值是什么？

**核心答案**

发现超过总数 1% 的 reads 时报 “FAIL”。

---

## 12. 接头含量

**本节问题列表**

- [FQ-079：Adapter Content 模块的核心作用是什么？](#fq-079-adapter-content-模块的核心作用是什么)
- [FQ-080：Adapter Content 模块的统计规则是什么？](#fq-080-adapter-content-模块的统计规则是什么)
- [FQ-081：Adapter Content 模块的正常结果特征是什么？](#fq-081-adapter-content-模块的正常结果特征是什么)
- [FQ-082：Adapter Content 模块出现警告或错误代表什么？](#fq-082-adapter-content-模块出现警告或错误代表什么)
- [FQ-083：Adapter Content 模块中检测到 Adapter 污染的处理方式是什么？](#fq-083-adapter-content-模块中检测到-adapter-污染的处理方式是什么)
- [FQ-084：Adapter Content 模块的 WARN 判定阈值是什么？](#fq-084-adapter-content-模块的-warn-判定阈值是什么)
- [FQ-085：Adapter Content 模块的 FAIL 判定阈值是什么？](#fq-085-adapter-content-模块的-fail-判定阈值是什么)

### FQ-079：Adapter Content 模块的核心作用是什么？

**核心答案**

衡量序列中两端 adapter 的情况。

---

### FQ-080：Adapter Content 模块的统计规则是什么？

**核心答案**

如果在 fastqc 分析的时候 - a 选项没有内容，则默认使用图例中的四种通用 adapter 序列进行统计；该模块针对接头序列的 Kmer 进行搜索。

---

### FQ-081：Adapter Content 模块的正常结果特征是什么？

**核心答案**

正常情况是趋于 0 的直线，也就是说序列两端 Adapter 已经去除干净。

---

### FQ-082：Adapter Content 模块出现警告或错误代表什么？

**核心答案**

任何插入片段小于测序长度的 Reads 达到一定比例的文库都会引发警告或错误，说明下游分析前需要进行接头 Trimming。

---

### FQ-083：Adapter Content 模块中检测到 Adapter 污染的处理方式是什么？

**核心答案**

需要先用 cutadapt 去接头。

---

### FQ-084：Adapter Content 模块的 WARN 判定阈值是什么？

**核心答案**

如果任何序列在所有读取中出现的次数超过 5%，报 “WARN”。

---

### FQ-085：Adapter Content 模块的 FAIL 判定阈值是什么？

**核心答案**

如果任何序列在所有读取中出现的次数超过 10%，报 “FAIL”。

---
