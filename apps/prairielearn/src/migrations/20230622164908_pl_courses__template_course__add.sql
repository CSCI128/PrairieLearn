ALTER TABLE pl_courses
ADD COLUMN IF NOT EXISTS template_course BOOLEAN NOT NULL DEFAULT FALSE;
