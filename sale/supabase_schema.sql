-- SALE Component 3 — Supabase Schema
-- Paste into: Supabase → SQL Editor → Run

-- ── Tables ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sensor_readings (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    student_id  TEXT        NOT NULL,
    bpm         REAL, spo2 REAL,
    gsr_norm    REAL, gsr_tonic REAL, gsr_phasic REAL,
    skin_temp_c REAL,
    source      TEXT DEFAULT 'demo'
);

CREATE TABLE IF NOT EXISTS stress_scores (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    student_id  TEXT        NOT NULL,
    sphys       REAL        NOT NULL,
    svis        REAL        NOT NULL,
    st          REAL        NOT NULL,
    e           REAL        NOT NULL,
    alert       TEXT        NOT NULL DEFAULT 'normal'
                            CHECK (alert IN ('normal','medium','high','disengaged'))
);

CREATE TABLE IF NOT EXISTS alert_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    student_id  TEXT        NOT NULL,
    alert_type  TEXT        NOT NULL,
    st          REAL, e REAL
);

-- ── Indexes ────────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_scores_ts  ON stress_scores (ts DESC);
CREATE INDEX IF NOT EXISTS idx_sensors_ts ON sensor_readings (ts DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_ts  ON alert_log (ts DESC);

-- ── Auto-cleanup function (delete data older than 24 hours) ───────────────────
CREATE OR REPLACE FUNCTION cleanup_old_data()
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM stress_scores   WHERE ts < now() - INTERVAL '24 hours';
    DELETE FROM sensor_readings WHERE ts < now() - INTERVAL '24 hours';
    DELETE FROM alert_log       WHERE ts < now() - INTERVAL '7 days';
END;
$$;

-- ── Schedule cleanup every hour ────────────────────────────────────────────────
-- NOTE: pg_cron must be enabled first.
-- Go to: Supabase Dashboard → Database → Extensions → search "pg_cron" → Enable
-- Then run the line below:

-- SELECT cron.schedule('cleanup-old-data', '0 * * * *', 'SELECT cleanup_old_data()');

-- If you prefer NOT to use pg_cron, you can call cleanup manually anytime:
-- SELECT cleanup_old_data();

-- ── Alert trigger ──────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION log_alert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.alert IN ('high','disengaged') THEN
        INSERT INTO alert_log (ts, student_id, alert_type, st, e)
        VALUES (NEW.ts, NEW.student_id, NEW.alert, NEW.st, NEW.e);
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_log_alert ON stress_scores;
CREATE TRIGGER trg_log_alert
    AFTER INSERT ON stress_scores
    FOR EACH ROW EXECUTE FUNCTION log_alert();

-- ── Realtime ───────────────────────────────────────────────────────────────────
ALTER PUBLICATION supabase_realtime ADD TABLE stress_scores;
ALTER PUBLICATION supabase_realtime ADD TABLE alert_log;

-- ── Row Level Security ─────────────────────────────────────────────────────────
ALTER TABLE stress_scores   ENABLE ROW LEVEL SECURITY;
ALTER TABLE sensor_readings ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_log       ENABLE ROW LEVEL SECURITY;

CREATE POLICY "insert_scores"  ON stress_scores   FOR INSERT WITH CHECK (true);
CREATE POLICY "insert_sensors" ON sensor_readings FOR INSERT WITH CHECK (true);
CREATE POLICY "read_scores"    ON stress_scores   FOR SELECT USING (auth.role() = 'authenticated');
CREATE POLICY "read_sensors"   ON sensor_readings FOR SELECT USING (auth.role() = 'authenticated');
CREATE POLICY "read_alerts"    ON alert_log       FOR SELECT USING (auth.role() = 'authenticated');

SELECT 'Schema ready. Remember to enable pg_cron if you want auto-cleanup.' AS note;
