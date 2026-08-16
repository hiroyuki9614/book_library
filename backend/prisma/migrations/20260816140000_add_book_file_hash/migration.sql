-- Add the content hash required by the current BookFile schema.
ALTER TABLE "book_files"
  ADD COLUMN "file_hash" VARCHAR(64) NOT NULL;

-- Keep duplicate uploads rejected at the database boundary.
CREATE UNIQUE INDEX "book_files_file_hash_key" ON "book_files"("file_hash");
