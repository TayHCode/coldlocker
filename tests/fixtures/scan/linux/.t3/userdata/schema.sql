CREATE TABLE scheduled_tasks(task_id TEXT PRIMARY KEY, title TEXT, prompt TEXT, enabled INTEGER, schedule_json TEXT, webhook_token TEXT, webhook_secret TEXT);
