-- ============================================================
-- 诊断：火山引擎实例被误标「已释放资源」的原因
-- 在【线上数据库】执行（SELECT，非破坏性）
-- ============================================================

-- 1) 该实例当前状态与归属（重点看 vpc_id / region / deleted_on_cloud / released_at / managed）
SELECT r.id, r.resource_id, r.resource_name, r.resource_type, r.account_id,
       r.vpc_id, r.region, r.deleted_on_cloud, r.released_at, r.last_sync_at, r.managed
FROM resources r
WHERE r.resource_id = 'i-yd3yl9gveovr6okjydi2';

-- 2) 所属火山账号的 VPC 白名单配置与 region（account_id 取自第 1 步）
SELECT a.id, a.name, a.provider, a.region, a.vpc_ids, a.enabled
FROM cloud_accounts a
WHERE a.id = (把第 1 步查到的 account_id 填到这里);

-- 3) 该账号下全部被标记「已释放」的资源（看是否批量误标，还是仅此一条）
SELECT resource_id, resource_name, resource_type, vpc_id, region, released_at
FROM resources
WHERE account_id = (同上 account_id) AND deleted_on_cloud = 1;

-- ============================================================
-- 判读：
--  A. 第 1 步 vpc_id 对应的账号 vpc_ids 白名单里【没有】它
--     -> 根因 = VPC 白名单过滤（最可能）。白名单外的资源被火山 provider 丢弃，
--        触发 sync 误标已释放。
--  B. 第 1 步 region 与第 2 步账号 region【不一致】
--     -> 根因 = region 不匹配（按错误 region 查询，整批查不到）。
--  C. 第 3 步显示该账号下多台实例被误标（不止一条）
--     -> 更可能是白名单/region 这类批量问题；若仅此一条，则基本锁定 VPC 白名单。
-- ============================================================
