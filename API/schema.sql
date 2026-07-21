-- devicemanagement schema, inferred from API/*/*.py SQL usage.
-- Loaded automatically by the mysql container via /docker-entrypoint-initdb.d/.

CREATE TABLE IF NOT EXISTS users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  user_id       VARCHAR(191) NOT NULL UNIQUE,
  username      VARCHAR(191) NOT NULL,
  email         VARCHAR(191),
  phone_number  VARCHAR(32),
  password_hash VARCHAR(255) NOT NULL,
  status        VARCHAR(32) NOT NULL DEFAULT 'Active',
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS families (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  family_name VARCHAR(191) NOT NULL,
  admin_uid   VARCHAR(191) NOT NULL,
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS user_families (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    VARCHAR(191) NOT NULL,
  family_id  INT NOT NULL,
  role       VARCHAR(32) NOT NULL DEFAULT 'Member',
  status     VARCHAR(32),
  start_time DATETIME NULL,
  end_time   DATETIME NULL,
  max_uses   INT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_user_family (user_id, family_id),
  KEY idx_family (family_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS family_invitations (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  family_id   INT NOT NULL,
  inviter_uid VARCHAR(191) NOT NULL,
  invitee_uid VARCHAR(191) NOT NULL,
  role        VARCHAR(32) NOT NULL DEFAULT 'Guest',
  status      VARCHAR(32) NOT NULL DEFAULT 'Pending',
  created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_invitee (invitee_uid),
  KEY idx_family (family_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS devices (
  device_id           VARCHAR(191) PRIMARY KEY,
  device_type         VARCHAR(64) NOT NULL,
  device_name         VARCHAR(191),
  status              VARCHAR(32) NOT NULL DEFAULT 'Active',
  last_action         VARCHAR(64),
  family_id           INT,
  gateway_id          VARCHAR(191),
  owner_user_id       VARCHAR(191),
  device_public_key   TEXT,
  gateway_public_key  TEXT,
  session_key_hash    VARCHAR(191),
  pairing_status      VARCHAR(32),
  paired_at           DATETIME NULL,
  revoked_at          DATETIME NULL,
  revoked_by          VARCHAR(191),
  revocation_reason   VARCHAR(255),
  physical_state      VARCHAR(32),
  battery             INT,
  rssi                INT,
  online_status       VARCHAR(32),
  last_command        VARCHAR(64),
  last_seen           DATETIME NULL,
  last_update          DATETIME NULL,
  updated_at          DATETIME NULL,
  created_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_family (family_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS audit_logs (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  command_id   VARCHAR(191),
  user_id      VARCHAR(191),
  u_id         INT,
  actor_id     VARCHAR(191),
  actor_type   VARCHAR(32),
  device_id    VARCHAR(191),
  family_id    INT,
  action       VARCHAR(64),
  parameters   JSON,
  raw_data     JSON,
  status       VARCHAR(32),
  decision     VARCHAR(32),
  reason       VARCHAR(255),
  prev_hash    CHAR(64),
  current_hash CHAR(64),
  hash         CHAR(64),
  timestamp    BIGINT,
  created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_device (device_id),
  KEY idx_family (family_id),
  KEY idx_timestamp (timestamp)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS guest_tokens (
  token_id        VARCHAR(191) PRIMARY KEY,
  token_hash      CHAR(64) NOT NULL,
  family_id       INT NOT NULL,
  device_id       VARCHAR(191),
  allowed_actions JSON,
  expires_at      DATETIME NOT NULL,
  used_count      INT NOT NULL DEFAULT 0,
  max_uses        INT NOT NULL DEFAULT 1,
  revoked         TINYINT(1) NOT NULL DEFAULT 0,
  created_by      VARCHAR(191),
  last_used_at    DATETIME NULL,
  created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_family (family_id),
  KEY idx_token_hash (token_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS control_commands (
  command_id       VARCHAR(191) PRIMARY KEY,
  family_id        INT NOT NULL,
  device_id        VARCHAR(191) NOT NULL,
  actor_id         VARCHAR(191),
  actor_type       VARCHAR(32),
  action           VARCHAR(32),
  parameters       JSON,
  control_mode     VARCHAR(16),
  target_topic     VARCHAR(255),
  request_payload  JSON,
  response_payload JSON,
  status           VARCHAR(32),
  reason           VARCHAR(255),
  created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  published_at     DATETIME NULL,
  completed_at     DATETIME NULL,
  KEY idx_device (device_id),
  KEY idx_family (family_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS device_telemetry (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  family_id       INT,
  device_id       VARCHAR(191) NOT NULL,
  command_id      VARCHAR(191),
  physical_state  VARCHAR(32),
  status          VARCHAR(32),
  telemetry_data  JSON,
  battery         INT,
  rssi            INT,
  raw_data        JSON,
  recorded_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_device_time (device_id, recorded_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS policy_rules (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  family_id  INT NOT NULL,
  device_id  VARCHAR(191),
  role       VARCHAR(32),
  action     VARCHAR(32),
  effect     VARCHAR(16) NOT NULL DEFAULT 'allow',
  enabled    TINYINT(1) NOT NULL DEFAULT 1,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_family (family_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
