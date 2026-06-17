TOOL_NAME_MAPPING = {
    "bailian_web_search": "联网搜索",
    "search_mcp": "联网搜索",
    "query_knowledge": "查询知识库",
    "consult_technical_expert": "咨询技术专家",
    "consult_project_business_expert": "咨询项目业务专家",
    "run_project_analysis_workflow": "执行项目分析流程",
    "identify_project_tool": "识别项目",
    "plan_project_investigation_tool": "生成分析计划",
    "analyze_project_stage_tool": "阶段取证分析",
    "analyze_project_tool": "深度分析与总结",
    "load_project_memory_tool": "读取项目记忆",
    "read_project_report_tool": "读取项目报告",
    "save_project_memory_tool": "保存项目记忆",
}


TOOL_PROGRESS_HINTS = {
    "consult_project_business_expert": "正在判断是否需要进入项目分析链路",
    "run_project_analysis_workflow": "正在启动项目分析流程",
    "identify_project_tool": "正在识别当前问题对应的项目",
    "plan_project_investigation_tool": "正在规划分析步骤和取证顺序",
    "analyze_project_stage_tool": "正在进行阶段取证分析",
    "analyze_project_tool": "正在进行深度分析并汇总结论",
    "load_project_memory_tool": "正在读取历史项目记忆",
    "read_project_report_tool": "正在读取已有项目报告",
    "save_project_memory_tool": "正在保存本次分析记忆",
}


AGENT_NAME_MAPPING = {
    "project_business_agent": "项目业务智能体",
    "technical_agent": "技术专家智能体",
    "technical_agent_kb_only": "知识库技术智能体",
}


def format_tool_call_html(tool_name: str) -> str:
    display_name = TOOL_NAME_MAPPING.get(tool_name, tool_name)
    progress_hint = TOOL_PROGRESS_HINTS.get(tool_name, f"正在执行: {display_name}")
    return f"""
<div class="tech-process-card tool-call">
    <div class="tech-process-header">
        <span class="tech-icon">🔡</span>
        <span class="tech-label">系统过程</span>
    </div>
    <div class="tech-process-body">
        <strong class="highlight">{progress_hint}</strong>
        <div class="tech-process-flow">
            <span class="tech-node source">调度中心</span>
            <span class="tech-arrow">→</span>
            <span class="tech-node target">{display_name}</span>
        </div>
    </div>
</div>
"""


def format_agent_update_html(agent_name: str) -> str:
    display_name = AGENT_NAME_MAPPING.get(agent_name, agent_name)
    return f"""
<div class="tech-process-card agent-update">
    <div class="tech-process-header">
        <span class="tech-icon">🧠</span>
        <span class="tech-label">智能体切换</span>
    </div>
    <div class="tech-process-body">
        <span class="tech-text">当前接管: <strong class="highlight">{display_name}</strong></span>
    </div>
</div>
"""
