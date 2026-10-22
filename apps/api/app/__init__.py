"""帳務損益系統 API。

分層（見開發文件 2.3）：
- entry/    入口：路由與 schema，不放業務邏輯
- guard/    守門：登入、權限、系統金鑰、稽核
- feature/  功能：帳務業務邏輯，只透過 repositories 存取資料
- seams/    交接縫：外部系統的介面、Mock 與真實實作
"""
