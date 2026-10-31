-- Проверка результата инициализации. Только чтение.
SELECT current_user, current_database();
SELECT tablename FROM pg_catalog.pg_tables
WHERE schemaname = 'public' ORDER BY tablename;
SELECT code, name FROM public.roles ORDER BY id;
SELECT count(*) AS permission_count FROM public.permissions;
SELECT count(*) AS user_count FROM public.users;
