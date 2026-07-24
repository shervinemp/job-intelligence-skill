"""lib/db package — re-exports for backward compatibility with the former lib/db.py."""

from .schema import (
    AUTH_WALLS_PATH,
    DB_DIR,
    DB_PATH,
    STAGES,
    _JOBS_COLS,
    _conn,
    _create_v3_tables,
    _import_legacy_auth_walls,
    _migrate_schema,
    get_conn,
)
from .jobs import (
    _insert_job,
    _normalize_url,
    _parse_remote_status,
    _parse_salary_currency,
    _parse_salary_max,
    _parse_salary_min,
    _row_to_job,
    add_job,
    advance_job,
    failure_stats,
    get_failed_jobs,
    get_job,
    get_jobs_by_stage,
    job_count,
    job_count_by_stage,
    next_pending_job,
    record_failure,
    search_jobs,
)
from .state import load_state, save_state
from .stages import (
    stage_count,
    stage_delete,
    stage_exists,
    stage_get,
    stage_list_all,
    stage_save,
)
from .docs import (
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
from .companies import company_get, company_list_jobs, company_search, company_upsert
from .contacts import contact_add, contact_list, contact_update
from .events import event_add, event_complete, event_list
from .settings import (
    search_threads_clear,
    search_threads_pending,
    search_threads_save,
    setting_get,
    setting_set,
)
from .pipeline import (
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