-- Persist the per-user reader-derived percentage when the reader can measure it.
ALTER TABLE "reading_infos"
  ADD COLUMN "progress_percentage" INTEGER;

ALTER TABLE "reading_infos"
  ADD CONSTRAINT "reading_infos_progress_percentage_check"
  CHECK ("progress_percentage" IS NULL OR ("progress_percentage" >= 0 AND "progress_percentage" <= 100));
