INSERT INTO "categories" ("name", "display_order", "is_active", "created_at", "updated_at")
VALUES ('未分類', 0, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT ("name") DO UPDATE
SET "display_order" = 0,
    "is_active" = true,
    "updated_at" = CURRENT_TIMESTAMP;
