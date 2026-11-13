ALTER TABLE "reading_infos"
  ADD COLUMN "save_sequence" BIGINT NOT NULL DEFAULT 0,
  ADD CONSTRAINT "reading_infos_save_sequence_non_negative_check"
  CHECK ("save_sequence" >= 0);
