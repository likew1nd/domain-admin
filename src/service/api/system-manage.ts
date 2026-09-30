import { request } from '../request';

export function fetchGetSettings() {
  return request<Api.SystemSetting.PublicSettings>({ url: '/system/settings' });
}

export function fetchGetAdminSettings() {
  return request<Api.SystemSetting.Settings>({ url: '/system/settings/admin' });
}

export function fetchUpdateSettings(data: Api.SystemSetting.SettingsEdit) {
  return request<Api.SystemSetting.Settings>({ url: '/system/settings', method: 'put', data });
}

export function fetchGetVersion(refresh = false) {
  return request<Api.SystemSetting.VersionInfo>({ url: '/system/version', params: { refresh } });
}

export function fetchStartUpdate() {
  return request<{ current: string }>({ url: '/system/update', method: 'post' });
}

export function fetchSendTestMail(to: string) {
  return request({ url: '/system/settings/testMail', method: 'post', data: { to } });
}

/** get role list */
export function fetchGetRoleList(params?: Api.SystemManage.RoleSearchParams) {
  return request<Api.SystemManage.RoleList>({
    url: '/systemManage/getRoleList',
    method: 'get',
    params
  });
}

/**
 * get all roles
 *
 * these roles are all enabled
 */
export function fetchGetAllRoles() {
  return request<Api.SystemManage.AllRole[]>({
    url: '/systemManage/getAllRoles',
    method: 'get'
  });
}

/** get user list */
export function fetchGetUserList(params?: Api.SystemManage.UserSearchParams) {
  return request<Api.SystemManage.UserList>({
    url: '/systemManage/getUserList',
    method: 'get',
    params
  });
}

/** get menu list */
export function fetchGetMenuList() {
  return request<Api.SystemManage.MenuList>({
    url: '/systemManage/getMenuList/v2',
    method: 'get'
  });
}

/** get all pages */
export function fetchGetAllPages() {
  return request<string[]>({
    url: '/systemManage/getAllPages',
    method: 'get'
  });
}

/** get menu tree */
export function fetchGetMenuTree() {
  return request<Api.SystemManage.MenuTree[]>({
    url: '/systemManage/getMenuTree',
    method: 'get'
  });
}

/** add user */
export function fetchAddUser(data: Api.SystemManage.UserEdit) {
  return request({ url: '/systemManage/addUser', method: 'post', data });
}

/** update user */
export function fetchUpdateUser(id: number, data: Api.SystemManage.UserEdit) {
  return request({ url: `/systemManage/updateUser/${id}`, method: 'put', data });
}

/** delete users */
export function fetchDeleteUsers(ids: number[]) {
  return request({ url: '/systemManage/deleteUsers', method: 'post', data: { ids } });
}

/** add role */
export function fetchAddRole(data: Api.SystemManage.RoleEdit) {
  return request({ url: '/systemManage/addRole', method: 'post', data });
}

/** update role */
export function fetchUpdateRole(id: number, data: Api.SystemManage.RoleEdit) {
  return request({ url: `/systemManage/updateRole/${id}`, method: 'put', data });
}

/** delete roles */
export function fetchDeleteRoles(ids: number[]) {
  return request({ url: '/systemManage/deleteRoles', method: 'post', data: { ids } });
}

/** get the menus and home authorized to a role */
export function fetchGetRoleMenuAuth(roleId: number) {
  return request<Api.SystemManage.RoleMenuAuth>({ url: `/systemManage/getRoleMenuAuth/${roleId}` });
}

/** update the menus and home authorized to a role */
export function fetchUpdateRoleMenuAuth(roleId: number, data: Api.SystemManage.RoleMenuAuth) {
  return request({ url: `/systemManage/updateRoleMenuAuth/${roleId}`, method: 'put', data });
}

/** get all buttons defined in menus */
export function fetchGetAllButtons() {
  return request<Api.SystemManage.ButtonOption[]>({ url: '/systemManage/getAllButtons' });
}

/** get the button codes authorized to a role */
export function fetchGetRoleButtonAuth(roleId: number) {
  return request<string[]>({ url: `/systemManage/getRoleButtonAuth/${roleId}` });
}

/** update the button codes authorized to a role */
export function fetchUpdateRoleButtonAuth(roleId: number, buttonCodes: string[]) {
  return request({ url: `/systemManage/updateRoleButtonAuth/${roleId}`, method: 'put', data: { buttonCodes } });
}

/** add menu */
export function fetchAddMenu(data: Api.SystemManage.MenuEdit) {
  return request({ url: '/systemManage/addMenu', method: 'post', data });
}

/** update menu */
export function fetchUpdateMenu(id: number, data: Api.SystemManage.MenuEdit) {
  return request({ url: `/systemManage/updateMenu/${id}`, method: 'put', data });
}

/** delete menus */
export function fetchDeleteMenus(ids: number[]) {
  return request({ url: '/systemManage/deleteMenus', method: 'post', data: { ids } });
}
