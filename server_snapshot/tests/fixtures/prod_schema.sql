CREATE TABLE users (
	id INTEGER NOT NULL, 
	username VARCHAR, 
	hashed_password VARCHAR, 
	role VARCHAR, updated_at DATETIME, created_at DATETIME, tenant_id INTEGER REFERENCES tenants(id), 
	PRIMARY KEY (id)
);
CREATE INDEX ix_users_id ON users (id);
CREATE UNIQUE INDEX ix_users_username ON users (username);
CREATE TABLE cards (
	id INTEGER NOT NULL, 
	title VARCHAR, 
	description VARCHAR, 
	status VARCHAR, 
	total_amount FLOAT, 
	store_location VARCHAR, 
	created_at DATETIME, 
	owner_id INTEGER, is_deleted BOOLEAN DEFAULT 0, client_id INTEGER REFERENCES clients(id), updated_at DATETIME, due_date DATE, priority INTEGER DEFAULT 0, sender_email VARCHAR(255), tenant_id INTEGER REFERENCES tenants(id), position INTEGER DEFAULT 0, writeoff_group_id INTEGER, paid_amount FLOAT DEFAULT 0.0, payment_due_date DATE, payment_status VARCHAR, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id)
);
CREATE INDEX ix_cards_title ON cards (title);
CREATE INDEX ix_cards_id ON cards (id);
CREATE TABLE card_attachments (
	id INTEGER NOT NULL, 
	file_name VARCHAR, 
	file_path VARCHAR, 
	uploaded_at DATETIME, 
	card_id INTEGER, updated_at DATETIME, created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(card_id) REFERENCES cards (id)
);
CREATE INDEX ix_card_attachments_id ON card_attachments (id);
CREATE TABLE card_checklists (
	id INTEGER NOT NULL, 
	company_name VARCHAR, 
	amount FLOAT, 
	is_paid BOOLEAN, 
	is_secondary_check BOOLEAN, 
	note VARCHAR, 
	card_id INTEGER, invoice_file_name VARCHAR(255), invoice_file_path VARCHAR(255), updated_at DATETIME, created_at DATETIME, supplier_id INTEGER REFERENCES suppliers(id), 
	PRIMARY KEY (id), 
	FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE CASCADE
);
CREATE INDEX ix_card_checklists_id ON card_checklists (id);
CREATE INDEX ix_card_checklists_company_name ON card_checklists (company_name);
CREATE TABLE transactions (
	id INTEGER NOT NULL, 
	date DATETIME, 
	company_name VARCHAR, 
	amount FLOAT, 
	store_location VARCHAR, 
	invoice_number VARCHAR, 
	invoice_date VARCHAR, 
	is_calculated BOOLEAN, 
	is_invoice_issued BOOLEAN, 
	is_written_off BOOLEAN, 
	is_secondary_check BOOLEAN, 
	print_status VARCHAR, 
	note VARCHAR, 
	card_id INTEGER, is_document BOOLEAN DEFAULT 0, is_invoice_doc BOOLEAN DEFAULT 0, is_bill_doc BOOLEAN DEFAULT 0, is_warehouse_writeoff BOOLEAN DEFAULT 0, updated_at DATETIME, created_at DATETIME, tenant_id INTEGER REFERENCES tenants(id), writeoff_group_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE SET NULL
);
CREATE INDEX ix_transactions_id ON transactions (id);
CREATE INDEX ix_transactions_company_name ON transactions (company_name);
CREATE TABLE clients (
	id INTEGER NOT NULL, 
	name VARCHAR, 
	phone VARCHAR, 
	email VARCHAR, 
	unp VARCHAR, 
	address VARCHAR, 
	contact_person VARCHAR, 
	note VARCHAR, 
	created_at DATETIME, updated_at DATETIME, tenant_id INTEGER REFERENCES tenants(id), 
	PRIMARY KEY (id)
);
CREATE INDEX ix_clients_unp ON clients (unp);
CREATE INDEX ix_clients_name ON clients (name);
CREATE INDEX ix_clients_id ON clients (id);
CREATE TABLE activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        card_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
        action VARCHAR NOT NULL,
        details TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    , tenant_id INTEGER REFERENCES tenants(id));
CREATE TABLE sqlite_sequence(name,seq);
CREATE TABLE tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR NOT NULL UNIQUE,
        color VARCHAR DEFAULT '#4f7cf5'
    , tenant_id INTEGER REFERENCES tenants(id));
CREATE TABLE card_tags (
        card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
        tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (card_id, tag_id)
    );
CREATE TABLE suppliers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name VARCHAR NOT NULL,
        phone VARCHAR,
        email VARCHAR,
        unp VARCHAR,
        address VARCHAR,
        contact_person VARCHAR,
        note VARCHAR,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME
    , tenant_id INTEGER REFERENCES tenants(id));
CREATE INDEX idx_cards_status ON cards(status);
CREATE INDEX idx_cards_is_deleted ON cards(is_deleted);
CREATE INDEX idx_cards_owner_id ON cards(owner_id);
CREATE INDEX idx_cards_client_id ON cards(client_id);
CREATE INDEX idx_cards_created_at ON cards(created_at);
CREATE INDEX idx_transactions_card_id ON transactions(card_id);
CREATE INDEX idx_transactions_is_document ON transactions(is_document);
CREATE INDEX idx_transactions_date ON transactions(date);
CREATE INDEX idx_activity_log_card_id ON activity_log(card_id);
CREATE INDEX idx_activity_log_user_id ON activity_log(user_id);
CREATE INDEX idx_activity_log_created_at ON activity_log(created_at);
CREATE INDEX idx_clients_unp ON clients(unp);
CREATE INDEX idx_clients_name ON clients(name);
CREATE TABLE tenants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    db_path TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE record_versions (
	id INTEGER NOT NULL, 
	table_name VARCHAR(50) NOT NULL, 
	record_id INTEGER NOT NULL, 
	version INTEGER NOT NULL, 
	data_snapshot TEXT NOT NULL, 
	changed_by INTEGER, 
	change_type VARCHAR(20) NOT NULL, 
	changed_at DATETIME, 
	tenant_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(changed_by) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_record_versions_record_id ON record_versions (record_id);
CREATE INDEX ix_record_versions_changed_at ON record_versions (changed_at);
CREATE INDEX ix_record_versions_id ON record_versions (id);
CREATE INDEX ix_record_versions_table_name ON record_versions (table_name);
CREATE TABLE custom_object_types (
	id INTEGER NOT NULL, 
	name VARCHAR(100), 
	label VARCHAR(200) NOT NULL, 
	icon VARCHAR(50), 
	tenant_id INTEGER, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_custom_object_types_id ON custom_object_types (id);
CREATE UNIQUE INDEX ix_custom_object_types_name ON custom_object_types (name);
CREATE TABLE store_locations (
	id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	address TEXT, 
	phone VARCHAR(50), 
	is_active BOOLEAN, 
	tenant_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (name), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_store_locations_id ON store_locations (id);
CREATE TABLE deal_statuses (
	id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	position INTEGER, 
	color VARCHAR(20), 
	is_active BOOLEAN, 
	tenant_id INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (name), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_deal_statuses_id ON deal_statuses (id);
CREATE TABLE workflows (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	description TEXT, 
	is_active BOOLEAN, 
	created_by INTEGER, 
	tenant_id INTEGER, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_workflows_id ON workflows (id);
CREATE TABLE webhooks (
	id INTEGER NOT NULL, 
	url VARCHAR(500) NOT NULL, 
	secret VARCHAR(200), 
	events TEXT NOT NULL, 
	is_active BOOLEAN, 
	tenant_id INTEGER, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_webhooks_id ON webhooks (id);
CREATE TABLE saved_views (
	id INTEGER NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	view_type VARCHAR(20), 
	target_page VARCHAR(50) NOT NULL, 
	filters TEXT, 
	sort_by VARCHAR(100), 
	sort_direction VARCHAR(10), 
	group_by VARCHAR(100), 
	is_default BOOLEAN, 
	created_by INTEGER, 
	tenant_id INTEGER, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_saved_views_id ON saved_views (id);
CREATE TABLE custom_field_defs (
	id INTEGER NOT NULL, 
	object_type_id INTEGER, 
	name VARCHAR(100) NOT NULL, 
	label VARCHAR(200) NOT NULL, 
	field_type VARCHAR(50) NOT NULL, 
	is_required BOOLEAN, 
	options TEXT, 
	relation_target VARCHAR(100), 
	position INTEGER, 
	tenant_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(object_type_id) REFERENCES custom_object_types (id) ON DELETE CASCADE, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_custom_field_defs_id ON custom_field_defs (id);
CREATE TABLE custom_records (
	id INTEGER NOT NULL, 
	object_type_id INTEGER, 
	created_by INTEGER, 
	tenant_id INTEGER, 
	created_at DATETIME, 
	updated_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(object_type_id) REFERENCES custom_object_types (id), 
	FOREIGN KEY(created_by) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_custom_records_id ON custom_records (id);
CREATE TABLE workflow_triggers (
	id INTEGER NOT NULL, 
	workflow_id INTEGER, 
	trigger_type VARCHAR(50) NOT NULL, 
	config TEXT NOT NULL, 
	tenant_id INTEGER, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workflow_id) REFERENCES workflows (id) ON DELETE CASCADE, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_workflow_triggers_id ON workflow_triggers (id);
CREATE TABLE workflow_steps (
	id INTEGER NOT NULL, 
	workflow_id INTEGER, 
	parent_step_id INTEGER, 
	step_type VARCHAR(50) NOT NULL, 
	action_type VARCHAR(50), 
	config TEXT NOT NULL, 
	position INTEGER, 
	tenant_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workflow_id) REFERENCES workflows (id) ON DELETE CASCADE, 
	FOREIGN KEY(parent_step_id) REFERENCES workflow_steps (id), 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_workflow_steps_id ON workflow_steps (id);
CREATE TABLE custom_field_values (
	id INTEGER NOT NULL, 
	record_id INTEGER, 
	field_def_id INTEGER, 
	value_text TEXT, 
	value_number NUMERIC, 
	value_boolean BOOLEAN, 
	value_date DATETIME, 
	value_json TEXT, 
	value_relation INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(record_id) REFERENCES custom_records (id) ON DELETE CASCADE, 
	FOREIGN KEY(field_def_id) REFERENCES custom_field_defs (id), 
	FOREIGN KEY(value_relation) REFERENCES custom_records (id)
);
CREATE INDEX ix_custom_field_values_id ON custom_field_values (id);
CREATE TABLE workflow_runs (
	id INTEGER NOT NULL, 
	workflow_id INTEGER, 
	trigger_id INTEGER, 
	status VARCHAR(20), 
	input_data TEXT, 
	output_data TEXT, 
	error TEXT, 
	started_at DATETIME, 
	completed_at DATETIME, 
	tenant_id INTEGER, 
	PRIMARY KEY (id), 
	FOREIGN KEY(workflow_id) REFERENCES workflows (id) ON DELETE SET NULL, 
	FOREIGN KEY(trigger_id) REFERENCES workflow_triggers (id) ON DELETE SET NULL, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_workflow_runs_id ON workflow_runs (id);
CREATE INDEX idx_cards_position ON cards(position);
CREATE TABLE writeoff_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR NOT NULL,
            client_id INTEGER,
            store_location VARCHAR,
            total_amount NUMERIC(12, 2),
            invoice_number VARCHAR,
            invoice_date VARCHAR,
            written_off BOOLEAN DEFAULT 0,
            tenant_id INTEGER,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
CREATE INDEX ix_writeoff_groups_id ON writeoff_groups (id);
CREATE INDEX ix_writeoff_groups_client_id ON writeoff_groups (client_id);
CREATE TABLE tasks (
	id INTEGER NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	description TEXT, 
	status VARCHAR(20), 
	due_date DATETIME, 
	priority INTEGER, 
	assignee_id INTEGER, 
	creator_id INTEGER, 
	card_id INTEGER, 
	client_id INTEGER, 
	completed_at DATETIME, 
	tenant_id INTEGER, 
	created_at DATETIME, 
	updated_at DATETIME, card_title_snapshot VARCHAR(255), client_name_snapshot VARCHAR(255), 
	PRIMARY KEY (id), 
	FOREIGN KEY(assignee_id) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(creator_id) REFERENCES users (id) ON DELETE SET NULL, 
	FOREIGN KEY(card_id) REFERENCES cards (id) ON DELETE SET NULL, 
	FOREIGN KEY(client_id) REFERENCES clients (id) ON DELETE SET NULL, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_tasks_id ON tasks (id);
CREATE INDEX ix_tasks_assignee_id ON tasks (assignee_id);
CREATE INDEX ix_tasks_status ON tasks (status);
CREATE INDEX ix_tasks_client_id ON tasks (client_id);
CREATE INDEX ix_tasks_card_id ON tasks (card_id);
CREATE INDEX ix_tasks_due_date ON tasks (due_date);
CREATE INDEX ix_tasks_title ON tasks (title);
CREATE TABLE notifications (
	id INTEGER NOT NULL, 
	user_id INTEGER NOT NULL, 
	type VARCHAR(30) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	details TEXT, 
	entity_type VARCHAR(20), 
	entity_id INTEGER, 
	is_read BOOLEAN, 
	read_at DATETIME, 
	tenant_id INTEGER, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_notifications_type ON notifications (type);
CREATE INDEX ix_notifications_user_id ON notifications (user_id);
CREATE INDEX ix_notifications_created_at ON notifications (created_at);
CREATE INDEX ix_notifications_is_read ON notifications (is_read);
CREATE INDEX ix_notifications_id ON notifications (id);
CREATE TABLE task_checklist_items (
	id INTEGER NOT NULL, 
	task_id INTEGER NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	is_done BOOLEAN, 
	position INTEGER, 
	tenant_id INTEGER, 
	created_at DATETIME, 
	PRIMARY KEY (id), 
	FOREIGN KEY(task_id) REFERENCES tasks (id) ON DELETE CASCADE, 
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);
CREATE INDEX ix_task_checklist_items_is_done ON task_checklist_items (is_done);
CREATE INDEX ix_task_checklist_items_id ON task_checklist_items (id);
CREATE INDEX ix_task_checklist_items_task_id ON task_checklist_items (task_id);
CREATE INDEX ix_card_attachments_card_id ON card_attachments(card_id);
CREATE INDEX ix_card_checklists_card_id ON card_checklists(card_id);
CREATE INDEX ix_cards_due_open ON cards(due_date) WHERE is_deleted=0 AND due_date IS NOT NULL;
CREATE UNIQUE INDEX ux_record_versions_t_r_v ON record_versions(table_name, record_id, version);
CREATE TABLE schema_migrations ( name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE UNIQUE INDEX uq_remainder_per_card ON transactions(card_id) WHERE card_id IS NOT NULL AND is_document = 0 AND (invoice_number IS NULL OR invoice_number = '') AND is_warehouse_writeoff = 0;
CREATE TABLE nakladnye (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    supplier_name TEXT,
    doc_type TEXT(20),
    doc_series TEXT(50),
    doc_number TEXT(50),
    doc_date TEXT(20),
    amount REAL,
    unload_address TEXT(255),
    store TEXT(100),
    is_verified INTEGER DEFAULT 0,
    is_paid INTEGER DEFAULT 0,
    status TEXT(20) DEFAULT 'new',
    photo_paths TEXT,
    created_by_bot INTEGER DEFAULT 0,
    tenant_id INTEGER REFERENCES tenants(id),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
, amount_no_vat REAL, is_arrived INTEGER DEFAULT 0, vat_amount REAL, products_json TEXT, excel_path TEXT);
CREATE INDEX ix_nakladnye_supplier_id ON nakladnye(supplier_id);
CREATE INDEX ix_nakladnye_supplier_name ON nakladnye(supplier_name);
CREATE INDEX ix_nakladnye_doc_number ON nakladnye(doc_number);
CREATE INDEX ix_nakladnye_store ON nakladnye(store);
CREATE INDEX ix_nakladnye_status ON nakladnye(status);
