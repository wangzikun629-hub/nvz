#!/usr/bin/env Rscript
# chart_plotly.R — 交互式图表生成器
#
# 用法（Python 通过 subprocess 调用）：
#   echo '<json>' | Rscript chart_plotly.R
#
# 输入（stdin）：JSON，字段见下方各函数
# 输出（stdout）：自包含 HTML 字符串，可直接放入 <iframe srcdoc>
#
# 依赖包：jsonlite, plotly, htmlwidgets
#   install.packages(c("jsonlite", "plotly", "htmlwidgets"))

suppressPackageStartupMessages({
  library(jsonlite)
  library(plotly)
  library(htmlwidgets)
})

# ── 工具函数 ────────────────────────────────────────────────────────────────

null_default <- function(x, default) if (is.null(x)) default else x

# 品牌色盘（与 matplotlib 版保持一致）
COLORS <- c("#1F5E9C", "#4F8CC9", "#D86F45", "#7C6AEB", "#2E8B57", "#B7791F")

common_layout <- function(p, title, xlab = "Sample", ylab = "") {
  p %>% layout(
    title  = list(text = title, font = list(size = 14, color = "#111827")),
    xaxis  = list(title = xlab, tickangle = -30, color = "#475569",
                  gridcolor = "#E6ECF2", zerolinecolor = "#D6DEE8"),
    yaxis  = list(title = ylab, color = "#475569",
                  gridcolor = "#E6ECF2", zerolinecolor = "#D6DEE8"),
    plot_bgcolor  = "#FFFFFF",
    paper_bgcolor = "#FFFFFF",
    font   = list(family = "Arial, sans-serif", color = "#334155"),
    margin = list(t = 60, b = 90, l = 60, r = 20),
    hoverlabel = list(bgcolor = "#1F2937", font = list(color = "white", size = 12))
  )
}

# ── 图类型：bar（单指标柱状图） ──────────────────────────────────────────────
# 输入 JSON 示例：
# {
#   "chart_type": "bar",
#   "title": "VZ20260427001 Mapping Rate",
#   "ylabel": "Mapping (%)",
#   "labels": ["S1", "S2", "S3"],
#   "values": [85.2, 92.1, 78.3]
# }
build_bar <- function(spec) {
  labels <- spec$labels
  values <- as.numeric(spec$values)
  ylabel <- null_default(spec$ylabel, "Value")
  title  <- null_default(spec$title, "")

  bar_colors <- rep_len(COLORS, length(labels))

  plot_ly(
    x = labels,
    y = values,
    type = "bar",
    marker = list(
      color     = bar_colors,
      line      = list(color = "white", width = 1.2),
      opacity   = 0.88
    ),
    text          = round(values, 2),
    textposition  = "outside",
    hovertemplate = paste0("<b>%{x}</b><br>", ylabel, ": %{y:.2f}<extra></extra>")
  ) %>%
    common_layout(title = title, ylab = ylabel) %>%
    layout(yaxis = list(range = list(0, max(values) * 1.14)))
}

# ── 图类型：line（折线图） ──────────────────────────────────────────────────
# 输入 JSON 与 bar 相同，chart_type 改为 "line"
build_line <- function(spec) {
  labels <- spec$labels
  values <- as.numeric(spec$values)
  ylabel <- null_default(spec$ylabel, "Value")
  title  <- null_default(spec$title, "")

  plot_ly(
    x    = labels,
    y    = values,
    type = "scatter",
    mode = "lines+markers+text",
    line   = list(color = "#1F5E9C", width = 2.5),
    marker = list(
      color  = "white",
      size   = 9,
      line   = list(color = "#1F5E9C", width = 2.2)
    ),
    fill      = "tozeroy",
    fillcolor = "rgba(31,94,156,0.07)",
    text         = round(values, 2),
    textposition = "top center",
    hovertemplate = paste0("<b>%{x}</b><br>", ylabel, ": %{y:.2f}<extra></extra>")
  ) %>%
    common_layout(title = title, ylab = ylabel) %>%
    layout(yaxis = list(range = list(0, max(values) * 1.16)))
}

# ── 图类型：grouped_bar（多指标分组柱状图，用于 AlignmentQC 样本对比） ────────
# 输入 JSON 示例：
# {
#   "chart_type": "grouped_bar",
#   "title": "VZ20260427001 AlignmentQC Comparison",
#   "ylabel": "Percent (%)",
#   "labels": ["S1", "S2"],
#   "metric_labels": ["Mapping", "Unique", "Duplicate", "chrMT/Pt"],
#   "matrix": [[85.2, 78.1, 12.3, 2.1], [92.1, 88.3, 8.5, 1.8]]
# }
build_grouped_bar <- function(spec) {
  labels        <- spec$labels
  metric_labels <- spec$metric_labels
  # fromJSON simplifyVector=TRUE 会把二维数组解析为矩阵（行=样本，列=指标）
  mat    <- as.matrix(spec$matrix)
  ylabel <- null_default(spec$ylabel, "Value")
  title  <- null_default(spec$title, "")

  p <- plot_ly()
  for (i in seq_along(metric_labels)) {
    col_values <- mat[, i]
    p <- add_trace(
      p,
      x    = labels,
      y    = col_values,
      name = metric_labels[i],
      type = "bar",
      marker = list(color = COLORS[(i - 1) %% length(COLORS) + 1],
                    line  = list(color = "white", width = 0.8)),
      hovertemplate = paste0(
        "<b>%{x}</b><br>", metric_labels[i], ": %{y:.2f}<extra></extra>"
      )
    )
  }

  p %>%
    common_layout(title = title, ylab = ylabel) %>%
    layout(
      barmode = "group",
      legend  = list(orientation = "h", y = -0.22,
                     xanchor = "center", x = 0.5),
      margin  = list(t = 60, b = 110, l = 60, r = 20)
    )
}

# ── 图类型：heatmap（相关性热图） ────────────────────────────────────────────
# 输入 JSON 示例：
# {
#   "chart_type": "heatmap",
#   "title": "VZ20260427001 Correlation Heatmap",
#   "labels": ["S1", "S2", "S3"],
#   "matrix": [[1.0, 0.95, 0.87], [0.95, 1.0, 0.91], [0.87, 0.91, 1.0]]
# }
build_heatmap <- function(spec) {
  labels <- spec$labels
  mat    <- as.matrix(spec$matrix)
  title  <- null_default(spec$title, "")

  # 与 matplotlib 版相同的五色渐变
  colorscale <- list(
    list(0,    "#2F5597"),
    list(0.25, "#8FB9DA"),
    list(0.5,  "#F7F9FC"),
    list(0.75, "#F6B26B"),
    list(1,    "#B03A2E")
  )

  plot_ly(
    x    = labels,
    y    = labels,
    z    = mat,
    type = "heatmap",
    colorscale    = colorscale,
    zmin = -1, zmax = 1,
    hovertemplate = "<b>%{y}</b> vs <b>%{x}</b><br>r = %{z:.3f}<extra></extra>",
    colorbar = list(
      title      = list(text = "Spearman r", font = list(size = 11)),
      tickfont   = list(size = 10),
      outlinewidth = 0,
      len        = 0.72
    )
  ) %>%
    layout(
      title  = list(text = title, font = list(size = 14, color = "#111827")),
      xaxis  = list(tickangle = -35, color = "#475569", side = "bottom"),
      yaxis  = list(autorange = "reversed", color = "#475569"),
      plot_bgcolor  = "#FFFFFF",
      paper_bgcolor = "#FFFFFF",
      font   = list(family = "Arial, sans-serif", color = "#334155"),
      margin = list(t = 60, b = 110, l = 110, r = 20),
      hoverlabel = list(bgcolor = "#1F2937", font = list(color = "white", size = 12))
    )
}

# ── 主流程 ───────────────────────────────────────────────────────────────────

main <- function() {
  # 从 stdin 读取 JSON
  input_lines <- readLines("stdin", warn = FALSE)
  if (length(input_lines) == 0) {
    stop("stdin is empty — expected JSON input")
  }
  input_json <- paste(input_lines, collapse = "\n")

  spec       <- fromJSON(input_json, simplifyVector = TRUE)
  chart_type <- null_default(spec$chart_type, "bar")

  fig <- switch(chart_type,
    bar         = build_bar(spec),
    line        = build_line(spec),
    grouped_bar = build_grouped_bar(spec),
    heatmap     = build_heatmap(spec),
    stop(paste("不支持的 chart_type:", chart_type,
               "— 可选值: bar, line, grouped_bar, heatmap"))
  )

  # 写自包含 HTML 到临时文件，再读出 cat 到 stdout
  tmp <- tempfile(fileext = ".html")
  on.exit(unlink(tmp), add = TRUE)

  saveWidget(
    widget       = fig,
    file         = tmp,
    selfcontained = TRUE,   # 所有 JS/CSS 内联，无外部依赖
    title        = null_default(spec$title, "Chart")
  )

  cat(paste(readLines(tmp, warn = FALSE), collapse = "\n"))
}

main()
