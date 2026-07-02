-- 迁移：Phase 1.5 修订（见 project_analysis_phase1.5_auto_promotion_revision.md 第一部分 / §9 / §5）
--
-- 背景：候选指标队列（原 Phase 1.5）和新增的"脚本公式转正祝福表"此前用进程内 JSON 文件 +
-- threading.Lock 存储，只在单 worker 下安全。生产确认要跑多 worker/多实例后，这两张表必须是
-- 跨进程一致的权威真值源，改落 MySQL，写入统一走原子 upsert（INSERT ... ON DUPLICATE KEY
-- UPDATE），出现次数用原子自增，避免读-改-写竞态。
--
-- 应用方式：
--   mysql -u<user> -p<password> its_db < 002_blessed_formula_and_candidate_metrics.sql
--
-- 两张表都是纯附加型的新表，不影响任何现有表结构或数据。

CREATE TABLE IF NOT EXISTS `candidate_metrics` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `candidate_key` VARCHAR(128) NOT NULL COMMENT '候选指标 slug，见 candidate_metric_service.slugify_metric_guess',
    `metric_guess` VARCHAR(128) NOT NULL COMMENT '候选指标猜测名（与 candidate_key 同源）',
    `label` VARCHAR(160) NOT NULL DEFAULT '' COMMENT '原始表头/描述文本',
    `unit_guess` VARCHAR(32) NOT NULL DEFAULT '',
    `status` VARCHAR(32) NOT NULL DEFAULT 'shadow' COMMENT 'shadow/pending_review/approved/approved_auto/rejected',
    `occurrence_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `distinct_project_count` INT UNSIGNED NOT NULL DEFAULT 0,
    `occurrences_json` MEDIUMTEXT NULL COMMENT '最近若干次观测的 JSON 数组（按仓库层 MAX_OCCURRENCES_PER_CANDIDATE 截断）',
    `promoted_metric_id` VARCHAR(128) NULL,
    `reviewed_by` VARCHAR(64) NULL,
    `review_note` VARCHAR(512) NULL,
    `blacklisted` TINYINT(1) NOT NULL DEFAULT 0,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_candidate_key` (`candidate_key`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Phase 1.5 候选指标队列（多 worker 权威存储）';

CREATE TABLE IF NOT EXISTS `blessed_formula_map` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `promotion_key` VARCHAR(191) NOT NULL COMMENT '(script_hash, metric_id, formula_variant) 拼接键，见 script_formula_promotion_service',
    `script_hash` CHAR(64) NOT NULL COMMENT 'SOP/workflow 脚本内容 sha256',
    `script_path_hint` VARCHAR(512) NOT NULL DEFAULT '' COMMENT '仅供人工排查参考，不参与转正判断（脚本可能被移动/改名）',
    `metric_id` VARCHAR(128) NOT NULL,
    `formula_variant` VARCHAR(64) NOT NULL DEFAULT 'unknown_variant',
    `numerator_field` VARCHAR(160) NOT NULL DEFAULT '',
    `denominator_field` VARCHAR(160) NOT NULL DEFAULT '',
    `verifier_contract` VARCHAR(64) NOT NULL DEFAULT 'display_value_only',
    `case_class` CHAR(1) NOT NULL COMMENT 'A/B/C/D/E，见方案第一部分 §3.3 分级表',
    `discovered_by` VARCHAR(32) NOT NULL DEFAULT 'code_semantics_static' COMMENT 'code_semantics_static / code_semantics_model',
    `status` VARCHAR(32) NOT NULL DEFAULT 'blessed' COMMENT 'blessed / pending_review / rejected',
    `blessed_by` VARCHAR(64) NOT NULL DEFAULT '' COMMENT '静态提取自动祝福时为 auto_static_extraction；人工审核时为 admin 用户标识',
    `blessed_at` DATETIME NULL,
    `created_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uk_promotion_key` (`promotion_key`),
    KEY `idx_metric_status` (`metric_id`, `status`),
    KEY `idx_script_hash` (`script_hash`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='脚本公式转正祝福表（多 worker 权威真值源，见修订方案第一部分 §5）';
