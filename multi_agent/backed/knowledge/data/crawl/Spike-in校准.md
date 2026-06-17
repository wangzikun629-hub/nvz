# Spike-in 校准

## 目录

- [SPI-001：CUT&Tag_Tool 工具中，Spike in 校准选项应如何选择？](#spi-001-cuttag_tool-工具中spike-in-校准选项应如何选择)
- [SPI-002：Spike in 校正的参考文献是什么？](#spi-002-spike-in-校正的参考文献是什么)
- [SPI-003：Spike in 校正的核心步骤是什么？](#spi-003-spike-in-校正的核心步骤是什么)
- [SPI-004：Spike in 校正时，以哪个样本作为校正基准？](#spi-004-spike-in-校正时以哪个样本作为校正基准)
- [SPI-005：Spike in 校正中，样本校正系数如何计算？](#spi-005-spike-in-校正中样本校正系数如何计算)
- [SPI-006：Spike in 校正中，用于后续分析的有效 reads 数如何计算？](#spi-006-spike-in-校正中用于后续分析的有效-reads-数如何计算)

## SPI-001：CUT&Tag_Tool 工具中，Spike in 校准选项应如何选择？

**核心答案**

若实验中没有添加 spike in，“是否 spike in 校准” 应当选择 “否”；如果实验中添加了 Spike in，该选项选择 “是”，并输入样本名称，否则会对数据分析产生影响。

---

## SPI-002：Spike in 校正的参考文献是什么？

**核心答案**

An Alternative Approach to ChIP-Seq Normalization Enables Detection of GenomeWide Changes in Histone H3 Lysine 27 Trimethylation upon EZH2 Inhibition

---

## SPI-003：Spike in 校正的核心步骤是什么？

**核心答案**

1. 原始数据过滤；

2. 过滤后的数据与 spike in 物种比对；

3. 比对上 spike in 物种的 reads 数选择最小的，作为最小样本；

4. 用最小的数值比上其他样本的能比对上 spike in 物种的 reads 数，得到一个小数；

5. 用这个小数乘以各样本过滤后的 reads 数，得到校准后的 reads 数，最小样本的值保持不变，用校准后的 reads 进行后续分析。

---

## SPI-004：Spike in 校正时，以哪个样本作为校正基准？

**核心答案**

以比对上 spike in 物种的 reads 数目最少的样本为基准。

---

## SPI-005：Spike in 校正中，样本校正系数如何计算？

**核心答案**

校正系数 p = 基准样本比对上 spike in 的 reads 数目 ÷ 其他样本比对上 spike in 的 reads 数目。

---

## SPI-006：Spike in 校正中，用于后续分析的有效 reads 数如何计算？

**核心答案**

其他样本过滤后的 clean reads 数 × 该样本对应的校正系数 p，得到用于后续常规分析的有效 reads 数；基准样本的 reads 数保持不变。

---
