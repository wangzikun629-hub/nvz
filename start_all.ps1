$python = "D:\nvz\kefu\venv\Scripts\python.exe"
$knowledgeMain = "D:\nvz\kefu\multi_agent\backed\knowledge\api\main.py"
$appMain = "D:\nvz\kefu\multi_agent\backed\app\api\main.py"
$env:PROJECT_BASE_DIRS = "sftp://wangzk@10.11.0.16:22/beegfs/Pipline_cloud/data_cloud/Result;sftp://wangzk@10.11.0.16:22/beegfs/Pipline_cloud/data_cloud/Snakemake_Sop"
$env:PROJECT_SEARCH_DEPTH = "2"
$env:PROJECT_WORKSPACE_DIR = "D:\nvz\kefu\project_workspaces"

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "& `"$python`" `"$knowledgeMain`""
)

Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "& `"$python`" `"$appMain`""
)
