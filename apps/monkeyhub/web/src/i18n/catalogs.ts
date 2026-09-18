import { hubCopy as hubEn, chatCopy as chatEn } from './messages.en';
import { hubCopy as hubZh, chatCopy as chatZh } from './messages.zh-CN';
export const hubCopyCatalog = { en: hubEn, 'zh-CN': hubZh } as const;
export const chatCopyCatalog = { en: chatEn, 'zh-CN': chatZh } as const;
