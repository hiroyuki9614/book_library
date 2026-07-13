-- Switch book file storage from local file path to Cloudflare R2 URL.
ALTER TABLE "book_files"
  RENAME COLUMN "file_path" TO "file_url";

ALTER TABLE "book_files"
  ALTER COLUMN "file_url" TYPE VARCHAR(2048);
