import { request } from '../request';

export interface CaptchaAnswer {
  captchaId?: string;
  captchaCode?: string;
}

/**
 * Login
 *
 * @param userName User name
 * @param password Password
 */
export function fetchLogin(userName: string, password: string, captcha: CaptchaAnswer = {}) {
  return request<Api.Auth.LoginToken>({
    url: '/auth/login',
    method: 'post',
    data: {
      userName,
      password,
      ...captcha
    }
  });
}

export function fetchCaptcha() {
  return request<{ captchaId: string; image: string }>({ url: '/auth/captcha' });
}

export function fetchSendEmailCode(data: { purpose: 'login' | 'reset' | 'register'; email: string } & CaptchaAnswer) {
  return request({ url: '/auth/emailCode', method: 'post', data });
}

export function fetchEmailLogin(email: string, code: string) {
  return request<Api.Auth.LoginToken>({ url: '/auth/emailLogin', method: 'post', data: { email, code } });
}

export function fetchRegister(
  data: { userName: string; password: string; email?: string; emailCode?: string } & CaptchaAnswer
) {
  return request<{ needApproval: boolean }>({ url: '/auth/register', method: 'post', data });
}

export function fetchResetPassword(data: { email: string; code: string; password: string }) {
  return request({ url: '/auth/resetPassword', method: 'post', data });
}

/** Get user info */
export function fetchGetUserInfo() {
  return request<Api.Auth.UserInfo>({ url: '/auth/getUserInfo' });
}

/**
 * Refresh token
 *
 * @param refreshToken Refresh token
 */
export function fetchRefreshToken(refreshToken: string) {
  return request<Api.Auth.LoginToken>({
    url: '/auth/refreshToken',
    method: 'post',
    data: {
      refreshToken
    }
  });
}

/**
 * return custom backend error
 *
 * @param code error code
 * @param msg error message
 */
export function fetchCustomBackendError(code: string, msg: string) {
  return request({ url: '/auth/error', params: { code, msg } });
}
