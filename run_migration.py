import asyncio
import sys
from db import get_db

# Set Windows selector event loop policy for async operations
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def run_migration():
    # First create incidents table (no dependencies). report_id links
    # incidents to the source resident report (reports.id) — the real FK
    # relationship; tracking_token only mirrors reports.tracking_id.
    incidents_sql = """
CREATE TABLE IF NOT EXISTS public.incidents (
    id                     TEXT PRIMARY KEY,
    report_id              BIGINT REFERENCES public.reports(id) ON DELETE SET NULL,
    category               TEXT NOT NULL DEFAULT '',
    severity               TEXT NOT NULL DEFAULT '',
    purok                  TEXT NOT NULL DEFAULT '',
    description            TEXT NOT NULL DEFAULT '',
    source                 TEXT NOT NULL DEFAULT '',
    reporter               TEXT NOT NULL DEFAULT '',
    time                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    status                 TEXT NOT NULL DEFAULT 'new',
    photos                 INTEGER NOT NULL DEFAULT 0,
    lat                    DOUBLE PRECISION,
    lng                    DOUBLE PRECISION,
    priority               TEXT NOT NULL DEFAULT 'Medium',
    notes                  JSONB NOT NULL DEFAULT '[]',
    rating                 INTEGER,
    anonymous              BOOLEAN NOT NULL DEFAULT false,
    tracking_token         TEXT,
    acknowledged_at        TIMESTAMPTZ,
    sla_breached           BOOLEAN NOT NULL DEFAULT false,
    duplicate_resolved     BOOLEAN NOT NULL DEFAULT false,
    related_to             JSONB NOT NULL DEFAULT '[]',
    escalated_to_captain   BOOLEAN NOT NULL DEFAULT false,
    escalated_reason       TEXT,
    escalated_at           TIMESTAMPTZ,
    reporter_safe          BOOLEAN NOT NULL DEFAULT false,
    reporter_safe_at       TIMESTAMPTZ,
    validation_label       TEXT,
    related_alert_id       TEXT,
    closed_reason          TEXT,
    verification_status    TEXT NOT NULL DEFAULT 'new',
    verified_by            TEXT,
    verified_at            TIMESTAMPTZ,
    unverified_reason      TEXT,
    category_history       JSONB NOT NULL DEFAULT '[]',
    assigned_team          TEXT,
    dispatch_id            TEXT,
    closure_history        JSONB NOT NULL DEFAULT '[]',
    resolved_at            TIMESTAMPTZ,
    iot_data               JSONB,
    feedback               TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

    # Then create dependent tables
    dependent_tables_sql = """
CREATE TABLE IF NOT EXISTS public.dispatches (
    id                     TEXT PRIMARY KEY,
    incident               TEXT NOT NULL REFERENCES public.incidents(id) ON DELETE CASCADE,
    team                   TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'pending',
    purok                  TEXT NOT NULL DEFAULT '',
    eta                    TEXT,
    photos                 INTEGER NOT NULL DEFAULT 0,
    assignee_type          TEXT NOT NULL DEFAULT 'tanod',
    dispatched_at          TIMESTAMPTZ,
    on_scene_at            TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.blotters (
    id                     TEXT PRIMARY KEY,
    original_incident_id   TEXT NOT NULL REFERENCES public.incidents(id) ON DELETE CASCADE,
    category               TEXT NOT NULL DEFAULT '',
    title                  TEXT NOT NULL DEFAULT '',
    description            TEXT NOT NULL DEFAULT '',
    severity               TEXT NOT NULL DEFAULT '',
    purok                  TEXT NOT NULL DEFAULT '',
    source                 TEXT NOT NULL DEFAULT '',
    incident_date_time     TIMESTAMPTZ NOT NULL,
    resolved_date_time     TIMESTAMPTZ,
    filed_date_time        TIMESTAMPTZ NOT NULL DEFAULT now(),
    converted_date_time    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    reporter               TEXT NOT NULL DEFAULT '',
    anonymous              BOOLEAN NOT NULL DEFAULT false,
    assigned_officer       TEXT NOT NULL DEFAULT '',
    recorded_by            TEXT NOT NULL DEFAULT '',
    lat                    DOUBLE PRECISION,
    lng                    DOUBLE PRECISION,
    location_label         TEXT NOT NULL DEFAULT '',
    resolution_summary     TEXT NOT NULL DEFAULT '',
    final_disposition      TEXT NOT NULL DEFAULT 'Resolved',
    disposition_note       TEXT,
    closure_reason         TEXT,
    citizen_rating         INTEGER NOT NULL DEFAULT 0,
    citizen_feedback       TEXT,
    photo_count            INTEGER NOT NULL DEFAULT 0,
    field_notes            JSONB NOT NULL DEFAULT '[]',
    evidence_references    JSONB NOT NULL DEFAULT '[]',
    record_status          TEXT NOT NULL DEFAULT 'active',
    amendments             JSONB NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS public.audit_trail (
    id                     TEXT PRIMARY KEY,
    timestamp              TIMESTAMPTZ NOT NULL DEFAULT now(),
    acting_user            TEXT NOT NULL DEFAULT '',
    action                 TEXT NOT NULL DEFAULT '',
    incident_id            TEXT REFERENCES public.incidents(id) ON DELETE CASCADE,
    blotter_id             TEXT REFERENCES public.blotters(id) ON DELETE CASCADE,
    result                 TEXT NOT NULL DEFAULT 'success',
    reason                 TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

    # Create indexes
    indexes_sql = """
CREATE INDEX IF NOT EXISTS idx_incidents_status ON public.incidents (status);
CREATE INDEX IF NOT EXISTS idx_incidents_report_id ON public.incidents (report_id);
CREATE INDEX IF NOT EXISTS idx_incidents_purok ON public.incidents (purok);
CREATE INDEX IF NOT EXISTS idx_incidents_category ON public.incidents (category);
CREATE INDEX IF NOT EXISTS idx_incidents_time ON public.incidents (time);
CREATE INDEX IF NOT EXISTS idx_incidents_source ON public.incidents (source);
CREATE INDEX IF NOT EXISTS idx_dispatches_incident ON public.dispatches (incident);
CREATE INDEX IF NOT EXISTS idx_dispatches_status ON public.dispatches (status);
CREATE INDEX IF NOT EXISTS idx_dispatches_team ON public.dispatches (team);
CREATE INDEX IF NOT EXISTS idx_blotters_original_incident ON public.blotters (original_incident_id);
CREATE INDEX IF NOT EXISTS idx_blotters_record_status ON public.blotters (record_status);
CREATE INDEX IF NOT EXISTS idx_audit_trail_incident ON public.audit_trail (incident_id);
CREATE INDEX IF NOT EXISTS idx_audit_trail_blotter ON public.audit_trail (blotter_id);
CREATE INDEX IF NOT EXISTS idx_audit_trail_timestamp ON public.audit_trail (timestamp);
"""

    # Create RLS policies
    rls_sql = """
ALTER TABLE public.incidents ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dispatches ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.blotters ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_trail ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role full access" ON public.incidents
    FOR ALL
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role full access" ON public.dispatches
    FOR ALL
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role full access" ON public.blotters
    FOR ALL
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Service role full access" ON public.audit_trail
    FOR ALL
    USING (true)
    WITH CHECK (true);
"""

    async with get_db() as conn:
        async with conn.cursor() as cur:
            # Execute incidents table first
            print("Creating incidents table...")
            await cur.execute(incidents_sql)
            print("Incidents table created")

            # Then dependent tables
            print("Creating dependent tables...")
            statements = dependent_tables_sql.split(';')
            for statement in statements:
                statement = statement.strip()
                if statement and not statement.startswith('--'):
                    try:
                        await cur.execute(statement)
                        print(f"Executed: {statement[:50]}...")
                    except Exception as e:
                        if "already exists" in str(e) or "duplicate" in str(e).lower():
                            print(f"Skipped (already exists): {statement[:50]}...")
                        else:
                            print(f"Error: {e}")
                            raise
            print("Dependent tables created")

            # Create indexes
            print("Creating indexes...")
            statements = indexes_sql.split(';')
            for statement in statements:
                statement = statement.strip()
                if statement:
                    try:
                        await cur.execute(statement)
                        print(f"Executed: {statement[:50]}...")
                    except Exception as e:
                        if "already exists" in str(e) or "duplicate" in str(e).lower():
                            print(f"Skipped (already exists): {statement[:50]}...")
                        else:
                            print(f"Error: {e}")
                            raise
            print("Indexes created")

            # Create RLS policies
            print("Creating RLS policies...")
            statements = rls_sql.split(';')
            for statement in statements:
                statement = statement.strip()
                if statement and not statement.startswith('--'):
                    try:
                        await cur.execute(statement)
                        print(f"Executed: {statement[:50]}...")
                    except Exception as e:
                        if "already exists" in str(e) or "duplicate" in str(e).lower():
                            print(f"Skipped (already exists): {statement[:50]}...")
                        else:
                            print(f"Error: {e}")
                            raise
            print("RLS policies created")

            print("Migration completed successfully!")

if __name__ == "__main__":
    asyncio.run(run_migration())