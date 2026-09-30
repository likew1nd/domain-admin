<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import type { UploadFile } from 'element-plus';
import { fetchGetAdminSettings, fetchGetAllRoles, fetchSendTestMail, fetchUpdateSettings } from '@/service/api';
import { useAppStore } from '@/store/modules/app';
import SystemLogo from '@/components/common/system-logo.vue';

defineOptions({ name: 'SystemSetting' });

const appStore = useAppStore();

const LOGO_MAX_SIZE = 200 * 1024;

const loading = ref(true);
const saving = ref(false);
const testMailTo = ref('');
const testMailSending = ref(false);
const smtpPassword = ref('');
const clearSmtpPassword = ref(false);
const roles = ref<Api.SystemManage.AllRole[]>([]);
const form = reactive<Api.SystemSetting.Settings>({
  systemTitle: '',
  logo: '',
  loginKeepDays: 7,
  captchaEnabled: false,
  lockMaxAttempts: 5,
  lockMinutes: 15,
  registerEnabled: false,
  registerNeedApproval: true,
  registerDefaultRole: 'R_USER',
  emailLoginEnabled: false,
  passwordResetEnabled: false,
  smtpHost: '',
  smtpPort: 465,
  smtpSsl: true,
  smtpUser: '',
  smtpFrom: '',
  hasSmtpPassword: false
});

const registerRoles = computed(() => roles.value.filter(role => role.roleCode !== 'R_SUPER'));
const mailReady = computed(() => Boolean(form.smtpHost.trim() && (form.smtpFrom.trim() || form.smtpUser.trim())));

async function loadSettings() {
  loading.value = true;
  const [settings, roleList] = await Promise.all([fetchGetAdminSettings(), fetchGetAllRoles()]);
  loading.value = false;
  if (!settings.error) Object.assign(form, settings.data);
  if (!roleList.error) roles.value = roleList.data;
}

function handleLogoChange(file: UploadFile) {
  const raw = file.raw;
  if (!raw) return;
  if (!raw.type.startsWith('image/')) {
    window.$message?.error('请选择图片文件');
    return;
  }
  if (raw.size > LOGO_MAX_SIZE) {
    window.$message?.error('图片不能超过 200 KB');
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    form.logo = String(reader.result);
  };
  reader.readAsDataURL(raw);
}

function toggleSmtpSsl(value: string | number | boolean) {
  if (value && form.smtpPort === 587) form.smtpPort = 465;
  if (!value && form.smtpPort === 465) form.smtpPort = 587;
}

async function handleSave() {
  if (!form.systemTitle.trim()) {
    window.$message?.warning('请输入系统名称');
    return;
  }
  if ((form.emailLoginEnabled || form.passwordResetEnabled) && !mailReady.value) {
    window.$message?.warning('开启邮箱验证码登录或找回密码前，请先填写 SMTP 服务器和发件人');
    return;
  }
  const { hasSmtpPassword: _, ...values } = form;
  let password: string | undefined;
  if (clearSmtpPassword.value) password = '';
  else if (smtpPassword.value) password = smtpPassword.value;
  saving.value = true;
  const { data, error } = await fetchUpdateSettings({ ...values, smtpPassword: password });
  saving.value = false;
  if (error) return;
  Object.assign(form, data);
  smtpPassword.value = '';
  clearSmtpPassword.value = false;
  await appStore.loadSystemSettings();
  window.$message?.success('系统设置已保存');
}

async function handleTestMail() {
  if (!testMailTo.value.trim()) {
    window.$message?.warning('请输入收件邮箱');
    return;
  }
  testMailSending.value = true;
  const { error } = await fetchSendTestMail(testMailTo.value.trim());
  testMailSending.value = false;
  if (!error) window.$message?.success('测试邮件已发送，请查收');
}

loadSettings();
</script>

<template>
  <div v-loading="loading" class="flex-col-stretch gap-16px overflow-auto pb-16px">
    <ElCard class="flex-shrink-0 card-wrapper">
      <template #header>
        <p class="text-16px font-medium">站点信息</p>
        <p class="mt-4px text-13px text-gray-500">
          登录页、侧边栏 Logo、加载页和浏览器标签页都会使用这里的名称和图标。
        </p>
      </template>
      <ElForm :model="form" label-width="140px" class="max-w-720px">
        <ElFormItem label="系统名称">
          <ElInput v-model="form.systemTitle" maxlength="30" show-word-limit />
        </ElFormItem>
        <ElFormItem label="站点图标">
          <div class="flex-y-center gap-16px">
            <div class="size-64px flex-center border border-gray-200 rd-8px p-6px dark:border-gray-600">
              <img v-if="form.logo" :src="form.logo" class="size-full object-contain" alt="logo" />
              <SystemLogo v-else class="size-full" />
            </div>
            <div class="flex-col gap-8px">
              <div class="flex gap-8px">
                <ElUpload
                  :auto-upload="false"
                  :show-file-list="false"
                  accept="image/png,image/jpeg,image/svg+xml,image/x-icon,image/webp,image/gif"
                  @change="handleLogoChange"
                >
                  <ElButton>上传图标</ElButton>
                </ElUpload>
                <ElButton v-if="form.logo" @click="form.logo = ''">恢复默认</ElButton>
              </div>
              <span class="text-12px text-gray-500">建议正方形 PNG / SVG，不超过 200 KB；同时用作浏览器标签页图标</span>
            </div>
          </div>
        </ElFormItem>
      </ElForm>
    </ElCard>

    <ElCard class="flex-shrink-0 card-wrapper">
      <template #header>
        <p class="text-16px font-medium">登录安全</p>
      </template>
      <ElForm :model="form" label-width="140px" class="max-w-720px">
        <ElFormItem label="图形验证码">
          <ElSwitch v-model="form.captchaEnabled" />
          <span class="ml-12px text-13px text-gray-500">账号密码登录时需要输入图形验证码</span>
        </ElFormItem>
        <ElFormItem label="登录失败锁定">
          <div class="flex flex-wrap items-center gap-8px text-14px">
            连续输错密码
            <ElInputNumber
              v-model="form.lockMaxAttempts"
              :min="0"
              :max="100"
              controls-position="right"
              class="w-110px"
            />
            次，锁定账号
            <ElInputNumber v-model="form.lockMinutes" :min="1" :max="1440" controls-position="right" class="w-110px" />
            分钟
          </div>
          <div class="w-full text-12px text-gray-500">
            次数填 0 表示不锁定；管理员在用户管理里重置密码会同时解除锁定
          </div>
        </ElFormItem>
        <ElFormItem label="登录保持天数">
          <ElInputNumber v-model="form.loginKeepDays" :min="1" :max="90" controls-position="right" class="w-110px" />
          <span class="ml-12px text-13px text-gray-500">超过该天数未使用需重新登录，对之后的新登录生效</span>
        </ElFormItem>
      </ElForm>
    </ElCard>

    <ElCard class="flex-shrink-0 card-wrapper">
      <template #header>
        <p class="text-16px font-medium">登录方式</p>
        <p class="mt-4px text-13px text-gray-500">账号密码登录始终可用；邮箱相关功能需要先在下方配置发信邮箱。</p>
      </template>
      <ElForm :model="form" label-width="140px" class="max-w-720px">
        <ElFormItem label="自助注册">
          <ElSwitch v-model="form.registerEnabled" />
          <span class="ml-12px text-13px text-gray-500">
            登录页显示“注册账号”；已配置发信邮箱时注册需验证邮箱，否则需输入图形验证码
          </span>
        </ElFormItem>
        <template v-if="form.registerEnabled">
          <ElFormItem label="注册需审核">
            <ElSwitch v-model="form.registerNeedApproval" />
            <span class="ml-12px text-13px text-gray-500">开启后新账号为禁用状态，需管理员在用户管理中启用</span>
          </ElFormItem>
          <ElFormItem label="注册默认角色">
            <ElSelect v-model="form.registerDefaultRole" class="w-240px">
              <ElOption
                v-for="role in registerRoles"
                :key="role.roleCode"
                :label="role.roleName"
                :value="role.roleCode"
              />
            </ElSelect>
          </ElFormItem>
        </template>
        <ElFormItem label="邮箱验证码登录">
          <ElSwitch v-model="form.emailLoginEnabled" :disabled="!mailReady && !form.emailLoginEnabled" />
          <span class="ml-12px text-13px text-gray-500">用户凭账号绑定的邮箱收取验证码登录</span>
        </ElFormItem>
        <ElFormItem label="邮箱找回密码">
          <ElSwitch v-model="form.passwordResetEnabled" :disabled="!mailReady && !form.passwordResetEnabled" />
          <span class="ml-12px text-13px text-gray-500">登录页显示“忘记密码”，通过邮箱验证码重置密码</span>
        </ElFormItem>
      </ElForm>
    </ElCard>

    <ElCard class="flex-shrink-0 card-wrapper">
      <template #header>
        <p class="text-16px font-medium">发信邮箱（SMTP）</p>
        <p class="mt-4px text-13px text-gray-500">
          用于发送登录、注册和找回密码验证码。QQ / 163 邮箱需在邮箱设置中开启 SMTP 并使用授权码作为密码。
        </p>
      </template>
      <ElForm :model="form" label-width="140px" class="max-w-720px">
        <ElFormItem label="SMTP 服务器">
          <ElInput v-model="form.smtpHost" placeholder="例如：smtp.qq.com" />
        </ElFormItem>
        <ElFormItem label="端口 / 加密">
          <div class="flex-y-center gap-12px">
            <ElInputNumber v-model="form.smtpPort" :min="1" :max="65535" controls-position="right" class="w-130px" />
            <ElSwitch v-model="form.smtpSsl" active-text="SSL" inactive-text="STARTTLS" @change="toggleSmtpSsl" />
          </div>
        </ElFormItem>
        <ElFormItem label="账号">
          <ElInput v-model="form.smtpUser" placeholder="通常是完整邮箱地址" autocomplete="off" />
        </ElFormItem>
        <ElFormItem label="密码 / 授权码">
          <div class="w-full flex-y-center gap-12px">
            <ElInput
              v-model="smtpPassword"
              type="password"
              show-password
              autocomplete="new-password"
              :disabled="clearSmtpPassword"
              :placeholder="form.hasSmtpPassword ? '已保存，留空表示不修改' : '请输入密码或授权码'"
            />
            <ElCheckbox v-if="form.hasSmtpPassword" v-model="clearSmtpPassword">清除</ElCheckbox>
          </div>
        </ElFormItem>
        <ElFormItem label="发件人邮箱">
          <ElInput v-model="form.smtpFrom" placeholder="留空则使用账号" />
        </ElFormItem>
        <ElFormItem label="发送测试邮件">
          <div class="w-full flex-y-center gap-12px">
            <ElInput v-model="testMailTo" placeholder="收件邮箱（先保存配置再测试）" />
            <ElButton :loading="testMailSending" @click="handleTestMail">发送</ElButton>
          </div>
        </ElFormItem>
      </ElForm>
    </ElCard>

    <div class="flex flex-shrink-0 justify-end">
      <ElButton type="primary" size="large" :loading="saving" @click="handleSave">保存设置</ElButton>
    </div>
  </div>
</template>

<style scoped></style>
