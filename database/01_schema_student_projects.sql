-- Лабораторная работа № 5. Первоначальная схема проекта.
-- Выполнять один раз: БД student_projects, пользователь spm_dev.
BEGIN;

DO $$
BEGIN
    IF current_database() <> 'student_projects' OR current_user <> 'spm_dev' THEN
        RAISE EXCEPTION 'Use database student_projects and role spm_dev';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_tables
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
    ) THEN
        RAISE EXCEPTION 'User tables already exist; initialization cancelled';
    END IF;
END;
$$;

SET LOCAL search_path TO public;

CREATE TABLE users (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email varchar(254) NOT NULL,
    password_hash text NOT NULL CHECK (length(password_hash) > 0),
    last_name varchar(100) NOT NULL CHECK (btrim(last_name) <> ''),
    first_name varchar(100) NOT NULL CHECK (btrim(first_name) <> ''),
    middle_name varchar(100),
    study_group varchar(50),
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT users_email_not_blank CHECK (btrim(email) <> '')
);
CREATE UNIQUE INDEX users_email_ci_uq ON users (lower(email));

CREATE TABLE roles (
    id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code varchar(40) NOT NULL UNIQUE,
    name varchar(120) NOT NULL UNIQUE
);

CREATE TABLE permissions (
    id smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code varchar(80) NOT NULL UNIQUE,
    name varchar(200) NOT NULL
);

CREATE TABLE user_roles (
    user_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    role_id smallint NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX user_roles_role_idx ON user_roles(role_id);

CREATE TABLE role_permissions (
    role_id smallint NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    permission_id smallint NOT NULL REFERENCES permissions(id) ON DELETE RESTRICT,
    PRIMARY KEY (role_id, permission_id)
);
CREATE INDEX role_permissions_permission_idx ON role_permissions(permission_id);

CREATE TABLE topics (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title varchar(300) NOT NULL CHECK (btrim(title) <> ''),
    description text NOT NULL DEFAULT '',
    created_by bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    approved_by bigint REFERENCES users(id) ON DELETE RESTRICT,
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT topics_approval_pair CHECK (
        (approved_by IS NULL AND approved_at IS NULL)
        OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)
    )
);

CREATE TABLE projects (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    topic_id bigint NOT NULL REFERENCES topics(id) ON DELETE RESTRICT,
    student_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    supervisor_id bigint REFERENCES users(id) ON DELETE RESTRICT,
    teacher_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    plan_approved_by bigint REFERENCES users(id) ON DELETE RESTRICT,
    plan_approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT projects_different_people CHECK (
        student_id <> teacher_id AND
        (supervisor_id IS NULL OR supervisor_id <> student_id)
    ),
    CONSTRAINT projects_plan_approval_pair CHECK (
        (plan_approved_by IS NULL AND plan_approved_at IS NULL)
        OR (plan_approved_by IS NOT NULL AND plan_approved_at IS NOT NULL)
    )
);
CREATE INDEX projects_topic_idx ON projects(topic_id);
CREATE INDEX projects_student_idx ON projects(student_id);
CREATE INDEX projects_supervisor_idx ON projects(supervisor_id);
CREATE INDEX projects_teacher_idx ON projects(teacher_id);

CREATE TABLE stages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id bigint NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    position integer NOT NULL CHECK (position > 0),
    title varchar(200) NOT NULL CHECK (btrim(title) <> ''),
    starts_at timestamptz NOT NULL,
    due_at timestamptz NOT NULL,
    status varchar(20) NOT NULL DEFAULT 'planned',
    completed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT stages_project_position_uq UNIQUE (project_id, position),
    CONSTRAINT stages_dates_check CHECK (due_at >= starts_at),
    CONSTRAINT stages_status_check CHECK (
        status IN ('planned', 'in_progress', 'under_review', 'revision', 'completed')
    ),
    CONSTRAINT stages_completion_pair CHECK (
        (status = 'completed' AND completed_at IS NOT NULL)
        OR (status <> 'completed' AND completed_at IS NULL)
    )
);
CREATE INDEX stages_due_idx ON stages(due_at) WHERE status <> 'completed';

CREATE TABLE material_versions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stage_id bigint NOT NULL REFERENCES stages(id) ON DELETE RESTRICT,
    version_no integer NOT NULL CHECK (version_no > 0),
    uploaded_by bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    original_filename varchar(255) NOT NULL CHECK (btrim(original_filename) <> ''),
    storage_key varchar(500) NOT NULL UNIQUE CHECK (btrim(storage_key) <> ''),
    media_type varchar(127) NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes > 0),
    sha256 varchar(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    uploaded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT material_versions_number_uq UNIQUE (stage_id, version_no)
);
CREATE INDEX material_versions_author_idx ON material_versions(uploaded_by);

CREATE TABLE reviews (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    material_version_id bigint NOT NULL UNIQUE
        REFERENCES material_versions(id) ON DELETE RESTRICT,
    reviewer_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    decision varchar(20) NOT NULL CHECK (decision IN ('accepted', 'revision')),
    remarks text NOT NULL DEFAULT '',
    reviewed_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT reviews_revision_remarks CHECK (
        decision <> 'revision' OR btrim(remarks) <> ''
    )
);
CREATE INDEX reviews_reviewer_idx ON reviews(reviewer_id);

CREATE TABLE notifications (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipient_id bigint NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    project_id bigint REFERENCES projects(id) ON DELETE RESTRICT,
    event_key varchar(200) NOT NULL,
    message text NOT NULL CHECK (btrim(message) <> ''),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at timestamptz,
    CONSTRAINT notifications_event_uq UNIQUE (recipient_id, event_key),
    CONSTRAINT notifications_read_date CHECK (read_at IS NULL OR read_at >= created_at)
);
CREATE INDEX notifications_recipient_idx ON notifications(recipient_id, created_at DESC);
CREATE INDEX notifications_project_idx ON notifications(project_id);

CREATE TABLE audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id bigint REFERENCES users(id) ON DELETE RESTRICT,
    action varchar(100) NOT NULL CHECK (btrim(action) <> ''),
    entity_type varchar(80) NOT NULL,
    entity_id bigint,
    details jsonb NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(details) = 'object'),
    occurred_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX audit_events_actor_idx ON audit_events(actor_id);
CREATE INDEX audit_events_entity_idx ON audit_events(entity_type, entity_id);

INSERT INTO roles (code, name) VALUES
('student', 'Студент'),
('supervisor', 'Руководитель учебного проекта'),
('teacher', 'Преподаватель дисциплины'),
('department', 'Представитель кафедры'),
('admin', 'Администратор ИС');

INSERT INTO permissions (code, name) VALUES
('users.manage', 'Управление учетными записями'),
('topics.manage', 'Ведение и утверждение тем'),
('projects.create', 'Регистрация проекта'),
('projects.read', 'Просмотр доступных проектов'),
('supervisors.assign', 'Назначение руководителя'),
('plans.propose', 'Предложение плана'),
('plans.approve', 'Утверждение плана'),
('materials.upload', 'Загрузка материалов'),
('reviews.write', 'Проверка материалов'),
('statuses.read', 'Просмотр состояния проекта'),
('reports.read', 'Формирование отчетности'),
('roles.manage', 'Управление ролями и разрешениями');

-- Разрешения не заменяют проверку принадлежности конкретного проекта.
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r CROSS JOIN permissions p
WHERE
 (r.code = 'student' AND p.code IN
  ('projects.create', 'projects.read', 'plans.propose', 'materials.upload', 'statuses.read'))
 OR
 (r.code = 'supervisor' AND p.code IN
  ('projects.read', 'plans.propose', 'plans.approve', 'reviews.write', 'statuses.read'))
 OR
 (r.code = 'teacher' AND p.code IN
  ('topics.manage', 'projects.create', 'projects.read', 'supervisors.assign',
   'plans.propose', 'plans.approve', 'reviews.write', 'statuses.read', 'reports.read'))
 OR
 (r.code = 'department' AND p.code IN
  ('topics.manage', 'projects.read', 'supervisors.assign', 'statuses.read', 'reports.read'))
 OR
 (r.code = 'admin' AND p.code IN ('users.manage', 'roles.manage'));

COMMIT;
