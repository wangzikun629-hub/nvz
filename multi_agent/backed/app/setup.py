from setuptools import setup, find_packages

setup(
    name="its_app",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "fastapi==0.135.2",
        "uvicorn==0.49.0",
        "python-dotenv",
        "pydantic",
        "pydantic-settings==2.14.1",
        "openai==2.41.0",
        "openai-agents==0.14.1",
        "mcp==1.27.2",
        "deepagents==0.5.7",
        "langgraph==1.1.10",
        "langchain-core==1.3.3",
        "langchain-openai==1.1.12",
        "pymysql",
        "dbutils",
        "pystun3"
    ],
)
