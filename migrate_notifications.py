import sqlite3

db = sqlite3.connect("database.sqlite")

db.execute("""
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tagger_id INTEGER NOT NULL,
    tagged_user_id INTEGER NOT NULL,
    source_type TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,

    CHECK (source_type IN ('post', 'comment')),

    UNIQUE(source_type, source_id, tagged_user_id)
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL,
    actor_id INTEGER NOT NULL,
    type TEXT NOT NULL,
    tag_id INTEGER,
    post_id INTEGER,
    comment_id INTEGER,
    is_read INTEGER DEFAULT 0 NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
)
""")

db.commit()
db.close()

print("Database migration completed.")