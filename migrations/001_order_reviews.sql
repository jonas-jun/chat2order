-- 주문 검수 revision, draft, 학습 정답 저장을 위한 스키마.
-- Supabase SQL Editor 또는 migration runner에서 실행한다.

alter table extraction_jobs
    add column if not exists review_status text not null default 'pending',
    add column if not exists current_confirmed_revision integer,
    add column if not exists reviewed_at timestamptz,
    add column if not exists reviewed_by text;

alter table training_data
    add column if not exists corrected_json jsonb,
    add column if not exists label_status text not null default 'unreviewed',
    add column if not exists reviewed_at timestamptz,
    add column if not exists reviewer_id text,
    add column if not exists error_tags text[],
    add column if not exists confirmed_revision integer;

create table if not exists order_review_versions (
    id uuid primary key default gen_random_uuid(),
    job_id uuid not null references extraction_jobs(id) on delete cascade,
    revision integer not null,
    status text not null check (status in ('confirmed', 'superseded')),
    snapshot_json jsonb not null,
    source_hash text not null,
    idempotency_key text not null unique,
    created_by text not null,
    created_at timestamptz not null default now(),
    confirmed_at timestamptz,
    unique (job_id, revision)
);

create index if not exists order_review_versions_job_idx
    on order_review_versions(job_id, revision desc);

create table if not exists order_review_drafts (
    job_id uuid primary key references extraction_jobs(id) on delete cascade,
    base_revision integer not null default 0,
    snapshot_json jsonb not null,
    updated_by text not null,
    updated_at timestamptz not null default now()
);

create or replace function save_order_review_draft(
    p_job_id uuid,
    p_user_id text,
    p_base_revision integer,
    p_snapshot jsonb
) returns jsonb
language plpgsql
as $$
declare
    v_current_revision integer;
begin
    select coalesce(current_confirmed_revision, 0)
      into v_current_revision
      from extraction_jobs
     where id = p_job_id
       and user_id = p_user_id;

    if not found then
        raise exception '검수할 작업을 찾을 수 없거나 권한이 없습니다.';
    end if;

    if v_current_revision <> coalesce(p_base_revision, 0) then
        raise exception '다른 검수본이 먼저 확정되었습니다. 최신 결과를 다시 불러와 주세요.';
    end if;

    insert into order_review_drafts (
        job_id, base_revision, snapshot_json, updated_by, updated_at
    ) values (
        p_job_id, p_base_revision, p_snapshot, p_user_id, now()
    )
    on conflict (job_id) do update
       set base_revision = excluded.base_revision,
           snapshot_json = excluded.snapshot_json,
           updated_by = excluded.updated_by,
           updated_at = excluded.updated_at;

    update extraction_jobs
       set review_status = 'in_progress'
     where id = p_job_id;

    return jsonb_build_object('saved', true, 'base_revision', p_base_revision);
end;
$$;

create or replace function confirm_order_review(
    p_job_id uuid,
    p_user_id text,
    p_base_revision integer,
    p_snapshot jsonb,
    p_labels jsonb,
    p_source_hash text,
    p_idempotency_key text
) returns jsonb
language plpgsql
as $$
declare
    v_job extraction_jobs%rowtype;
    v_existing order_review_versions%rowtype;
    v_revision integer;
    v_label jsonb;
    v_training_id uuid;
    v_updated_count integer;
begin
    select * into v_job
      from extraction_jobs
     where id = p_job_id
       and user_id = p_user_id
     for update;

    if not found then
        raise exception '검수할 작업을 찾을 수 없거나 권한이 없습니다.';
    end if;

    select * into v_existing
      from order_review_versions
     where idempotency_key = p_idempotency_key;

    if found then
        if v_existing.job_id <> p_job_id then
            raise exception '잘못된 idempotency key입니다.';
        end if;
        return jsonb_build_object(
            'revision', v_existing.revision,
            'snapshot_json', v_existing.snapshot_json,
            'idempotent_replay', true
        );
    end if;

    if coalesce(v_job.current_confirmed_revision, 0) <> coalesce(p_base_revision, 0) then
        raise exception '다른 검수본이 먼저 확정되었습니다. 최신 결과를 다시 불러와 주세요.';
    end if;

    v_revision := coalesce(v_job.current_confirmed_revision, 0) + 1;

    update order_review_versions
       set status = 'superseded'
     where job_id = p_job_id
       and status = 'confirmed';

    insert into order_review_versions (
        job_id, revision, status, snapshot_json, source_hash,
        idempotency_key, created_by, confirmed_at
    ) values (
        p_job_id, v_revision, 'confirmed', p_snapshot, p_source_hash,
        p_idempotency_key, p_user_id, now()
    );

    for v_label in select * from jsonb_array_elements(p_labels)
    loop
        v_training_id := (v_label->>'training_data_id')::uuid;
        update training_data
           set corrected_json = v_label->'corrected_json',
               label_status = v_label->>'label_status',
               is_verified = (v_label->>'label_status') in (
                   'accepted', 'corrected', 'no_order_confirmed'
               ),
               reviewed_at = now(),
               reviewer_id = p_user_id,
               confirmed_revision = v_revision
         where id = v_training_id
           and job_id = p_job_id;
        get diagnostics v_updated_count = row_count;
        if v_updated_count <> 1 then
            raise exception '학습 데이터 레코드를 찾을 수 없습니다: %', v_training_id;
        end if;
    end loop;

    update extraction_jobs
       set review_status = 'confirmed',
           current_confirmed_revision = v_revision,
           reviewed_at = now(),
           reviewed_by = p_user_id,
           total_orders = coalesce(jsonb_array_length(p_snapshot->'orders'), 0)
     where id = p_job_id;

    delete from order_review_drafts where job_id = p_job_id;

    return jsonb_build_object(
        'revision', v_revision,
        'snapshot_json', p_snapshot,
        'idempotent_replay', false
    );
end;
$$;
