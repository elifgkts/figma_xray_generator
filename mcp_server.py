import json
from typing import List, Optional
from mcp.server.fastmcp import FastMCP

# Senin projendeki mevcut iş mantığı fonksiyonlarını içeri aktarıyoruz
from services.jira_client import JiraClient, JiraAuthConfig
from services.jira_audit import build_audit_jql, audit_issues_from_search_results

# Teknocan'ın tanıyacağı MCP sunucusunu başlatıyoruz
mcp = FastMCP("Çok Kaynaklı Analiz Sunucusu")


@mcp.tool()
def audit_jira_projects(
    project_keys: List[str],
    jira_base_url: str,
    jira_username: str,
    jira_api_token: str,
    filter_mode: str = "Tarih Aralığı",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    max_issues: int = 50,
    deep_link_analysis: bool = False
) -> str:
    """
    Belirtilen projeler ve tarihler arasında Jira'da kalite denetimi (Audit) yapar.
    Story ve Task'ların analize hazır olma durumunu (Readiness Score) hesaplar.
    """

    # 1. Kullanıcıdan gelen bilgilerle Jira bağlantısını kur
    auth_config = JiraAuthConfig(
        base_url=jira_base_url,
        deployment_type="cloud",  # Gerekirse bunu da parametre yapabiliriz
        email=jira_username,
        api_token=jira_api_token,
        verify_ssl=True
    )
    jira_client = JiraClient(auth_config)

    # 2. JQL oluştur ve Jira'dan taskları çek
    all_issues = []
    per_project_limit = max(1, int(max_issues / max(1, len(project_keys))))

    for project_key in project_keys:
        jql = build_audit_jql(
            project_keys=[project_key.strip()],
            filter_mode=filter_mode,
            start_date=start_date,
            end_date=end_date,
            issue_types=["Story", "Task", "Sub-task"],
            sprint_ids=None
        )
        # Senin mevcut paginated arama fonksiyonun
        issues = jira_client.search_issues_paginated(
            jql=jql,
            fields=["summary", "description", "issuetype",
                    "priority", "status", "created", "updated"],
            limit=per_project_limit,
        )
        all_issues.extend(issues)

    if not all_issues:
        return json.dumps({"status": "warning", "message": "Belirtilen filtrelerle Jira'da issue bulunamadı."})

    # 3. Taskları analiz et (arka planda DataFrame oluşturacak)
    df = audit_issues_from_search_results(
        jira_client=jira_client,
        issues=all_issues[:max_issues],
        include_attachment_contents=False,
        filter_mode=filter_mode,
        start_date=start_date,
        end_date=end_date,
        deep_link_analysis=deep_link_analysis,
        figma_client=None,
        confluence_client=None
    )

    # 4. DataFrame'i Teknocan'ın (LLM'in) anlayacağı formata (JSON) çevirip geri dön
    result_json = df.to_dict(orient="records")

    return json.dumps({
        "status": "success",
        "total_analyzed": len(df),
        "data": result_json
    }, ensure_ascii=False)


if __name__ == "__main__":
    # Sunucuyu standart giriş/çıkış (stdio) üzerinden çalıştırır
    mcp.run(transport="stdio")
