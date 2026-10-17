-- 个人工作日志笔记本系统 · 云端 MySQL 表结构
-- 与客户端本地 SQLite 结构保持一致，仅去掉 change_log（变更记录只存在于客户端）
-- 执行：mysql -u root -p < schema.sql

CREATE DATABASE IF NOT EXISTS `notebook`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `notebook`;

CREATE TABLE IF NOT EXISTS `tree` (
  `id`          VARCHAR(36)  NOT NULL COMMENT 'UUID 主键',
  `parent_id`   VARCHAR(36)  NULL     COMMENT '父节点 id，顶级为 NULL',
  `name`        VARCHAR(512) NOT NULL COMMENT '分支全名，含自动生成的日期前缀',
  `sort`        BIGINT       NOT NULL DEFAULT 0 COMMENT '排序号',
  `create_time` BIGINT       NOT NULL COMMENT '创建时间戳(ms)',
  `update_time` BIGINT       NOT NULL COMMENT '修改时间戳(ms)',
  `is_deleted`  TINYINT      NOT NULL DEFAULT 0 COMMENT '0 未删除 1 已删除',
  `sync_status` VARCHAR(16)  NOT NULL DEFAULT 'synced' COMMENT 'unsynced/synced',
  PRIMARY KEY (`id`),
  KEY `idx_parent` (`parent_id`, `sort`),
  KEY `idx_update` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='目录分支';

CREATE TABLE IF NOT EXISTS `work_log` (
  `id`          VARCHAR(36)  NOT NULL COMMENT 'UUID 主键',
  `tree_id`     VARCHAR(36)  NULL     COMMENT '所属分支 id',
  `title`       VARCHAR(512) NOT NULL DEFAULT '' COMMENT '日志标题',
  `content`     MEDIUMTEXT   NULL     COMMENT '日志正文(Markdown)',
  `create_time` BIGINT       NOT NULL COMMENT '创建时间戳(ms)',
  `update_time` BIGINT       NOT NULL COMMENT '修改时间戳(ms)',
  `is_deleted`  TINYINT      NOT NULL DEFAULT 0 COMMENT '0 未删除 1 已删除',
  `sync_status` VARCHAR(16)  NOT NULL DEFAULT 'synced' COMMENT 'unsynced/synced',
  PRIMARY KEY (`id`),
  KEY `idx_tree` (`tree_id`, `update_time`),
  KEY `idx_update` (`update_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='工作日志';
