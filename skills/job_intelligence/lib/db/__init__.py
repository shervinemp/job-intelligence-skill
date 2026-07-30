"""lib/db package — re-exports for backward compatibility with the former lib/db.py."""

__all__ = [
    # schema
    "AUTH_WALLS_PATH", "DB_DIR", "DB_PATH", "STAGES", "get_conn",
    # jobs
    "add_job", "advance_job", "find_duplicate", "failure_stats",
    "get_failed_jobs", "get_job", "get_jobs_by_stage",
    "job_count", "job_count_by_stage", "next_pending_job",
    "record_failure", "search_jobs",
    # state
    "load_state", "save_state",
    # stages
    "stage_count", "stage_delete", "stage_exists", "stage_get",
    "stage_list_all", "stage_save",
    # docs
    "app_delete_all", "app_get", "app_list", "app_list_job_ids", "app_save",
    "desc_exists", "desc_get", "desc_list_ids", "desc_save",
    "doc_delete_all", "doc_exists", "doc_get", "doc_list_files",
    "doc_list_ids", "doc_save",
    # companies / contacts / events
    "company_get", "company_list_jobs", "company_search", "company_upsert",
    "contact_add", "contact_list", "contact_update",
    "attempt_add", "attempt_list",
    "event_add", "event_complete", "event_list",
    # settings
    "search_threads_clear", "search_threads_pending", "search_threads_save",
    "setting_get", "setting_set",
    # pipeline
    "add", "advance", "close", "get_by_stage", "get_failed",
    "job_id", "load", "next_pending", "pipeline_status", "save",
]

from .schema import (  # noqa: F401
    AUTH_WALLS_PATH,
    DB_DIR,
    DB_PATH,
    STAGES,
    get_conn,
)
from .jobs import (  # noqa: F401
    add_job,
    advance_job,
    failure_stats,
    find_duplicate,
    get_failed_jobs,
    get_job,
    get_jobs_by_stage,
    job_count,
    job_count_by_stage,
    next_pending_job,
    record_failure,
    search_jobs,
)
from .state import load_state, save_state  # noqa: F401
from .stages import (  # noqa: F401
    stage_count,
    stage_delete,
    stage_exists,
    stage_get,
    stage_list_all,
    stage_save,
)
from .docs import (  # noqa: F401
    app_delete_all,
    app_get,
    app_list,
    app_list_job_ids,
    app_save,
    desc_exists,
    desc_get,
    desc_list_ids,
    desc_save,
    doc_delete_all,
    doc_exists,
    doc_get,
    doc_list_files,
    doc_list_ids,
    doc_save,
)
from .companies import company_get, company_list_jobs, company_search, company_upsert  # noqa: F401
from .contacts import contact_add, contact_list, contact_update  # noqa: F401
from .contacts import attempt_add, attempt_list  # noqa: F401
from .events import event_add, event_complete, event_list  # noqa: F401
from .settings import (  # noqa: F401
    search_threads_clear,
    search_threads_pending,
    search_threads_save,
    setting_get,
    setting_set,
)
from .pipeline import (  # noqa: F401
    add,
    advance,
    close,
    get_by_stage,
    get_failed,
    job_id,
    load,
    next_pending,
    pipeline_status,
    save,
)