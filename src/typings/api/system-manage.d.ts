declare namespace Api {
  namespace SystemSetting {
    /** readable before login */
    interface PublicSettings {
      systemTitle: string;
      logo: string;
      captchaEnabled: boolean;
      registerEnabled: boolean;
      /** SMTP is configured, so registration must verify the email */
      registerEmailRequired: boolean;
      emailLoginEnabled: boolean;
      passwordResetEnabled: boolean;
    }

    interface Settings {
      systemTitle: string;
      logo: string;
      loginKeepDays: number;
      captchaEnabled: boolean;
      lockMaxAttempts: number;
      lockMinutes: number;
      registerEnabled: boolean;
      registerNeedApproval: boolean;
      registerDefaultRole: string;
      emailLoginEnabled: boolean;
      passwordResetEnabled: boolean;
      smtpHost: string;
      smtpPort: number;
      smtpSsl: boolean;
      smtpUser: string;
      smtpFrom: string;
      hasSmtpPassword: boolean;
    }

    /** `smtpPassword` is omitted to keep the saved password */
    type SettingsEdit = Omit<Settings, 'hasSmtpPassword'> & { smtpPassword?: string };

    /** `failed` means the last online update did not finish, `log` holds its tail */
    interface UpdateStatus {
      state: 'idle' | 'running' | 'success' | 'failed';
      time: number;
      log?: string;
    }

    interface VersionInfo {
      current: string;
      latest: string;
      hasUpdate: boolean;
      releaseNotes: string;
      releaseUrl: string;
      publishedAt: string;
      checkError: string;
      /** the docker updater service is running, so online update is possible */
      updateSupported: boolean;
      updateStatus: UpdateStatus;
    }
  }

  /**
   * namespace SystemManage
   *
   * backend api module: "systemManage"
   */
  namespace SystemManage {
    type CommonSearchParams = Pick<Common.PaginatingCommonParams, 'current' | 'size'>;

    /** role */
    type Role = Common.CommonRecord<{
      /** role name */
      roleName: string;
      /** role code */
      roleCode: string;
      /** role description */
      roleDesc: string;
    }>;

    /** role search params */
    type RoleSearchParams = CommonType.RecordNullable<
      Pick<Api.SystemManage.Role, 'roleName' | 'roleCode' | 'status'> & CommonSearchParams
    >;

    /** role list */
    type RoleList = Common.PaginatingQueryRecord<Role>;

    /** all role */
    type AllRole = Pick<Role, 'id' | 'roleName' | 'roleCode'>;

    /**
     * user gender
     *
     * - "1": "male"
     * - "2": "female"
     */
    type UserGender = '1' | '2';

    /** user */
    type User = Common.CommonRecord<{
      /** user name */
      userName: string;
      /** user gender */
      userGender: UserGender | undefined;
      /** user nick name */
      nickName: string;
      /** user phone */
      userPhone: string;
      /** user email */
      userEmail: string;
      /** user role code collection */
      userRoles: string[];
    }>;

    /** user search params */
    type UserSearchParams = CommonType.RecordNullable<
      Pick<Api.SystemManage.User, 'userName' | 'userGender' | 'nickName' | 'userPhone' | 'userEmail' | 'status'> &
        CommonSearchParams
    >;

    /** user list */
    type UserList = Common.PaginatingQueryRecord<User>;

    /**
     * menu type
     *
     * - "1": directory
     * - "2": menu
     */
    type MenuType = '1' | '2';

    type MenuButton = {
      /**
       * button code
       *
       * it can be used to control the button permission
       */
      code: string;
      /** button description */
      desc: string;
    };

    /**
     * icon type
     *
     * - "1": iconify icon
     * - "2": local icon
     */
    type IconType = '1' | '2';

    type MenuPropsOfRoute = Pick<
      import('vue-router').RouteMeta,
      | 'i18nKey'
      | 'keepAlive'
      | 'constant'
      | 'order'
      | 'href'
      | 'hideInMenu'
      | 'activeMenu'
      | 'multiTab'
      | 'fixedIndexInTab'
      | 'query'
    >;

    type Menu = Common.CommonRecord<{
      /** parent menu id */
      parentId: number;
      /** menu type */
      menuType: MenuType;
      /** menu name */
      menuName: string;
      /** route name */
      routeName: string;
      /** route path */
      routePath: string;
      /** component */
      component?: string;
      /** iconify icon name or local icon name */
      icon: string;
      /** icon type */
      iconType: IconType;
      /** buttons */
      buttons?: MenuButton[] | null;
      /** children menu */
      children?: Menu[] | null;
    }> &
      MenuPropsOfRoute;

    /** menu list */
    type MenuList = Common.PaginatingQueryRecord<Menu>;

    type MenuTree = {
      id: number;
      label: string;
      pId: number;
      /** route name */
      routeName: string;
      /** whether the menu is a page that can be used as home */
      isPage: boolean;
      children?: MenuTree[];
    };

    type EditableFields = 'id' | 'createBy' | 'createTime' | 'updateBy' | 'updateTime';

    /** user add / update params, password is optional when updating */
    type UserEdit = Omit<User, EditableFields> & { password?: string };

    /** role add / update params */
    type RoleEdit = Pick<Role, 'roleName' | 'roleCode' | 'roleDesc' | 'status'>;

    /** menu add / update params */
    type MenuEdit = Omit<Menu, EditableFields | 'children'>;

    /** menus and home authorized to a role */
    type RoleMenuAuth = {
      home: string;
      menuIds: number[];
    };

    /** button defined in menus */
    type ButtonOption = MenuButton & {
      /** the menu the button belongs to */
      menuName: string;
    };
  }
}
