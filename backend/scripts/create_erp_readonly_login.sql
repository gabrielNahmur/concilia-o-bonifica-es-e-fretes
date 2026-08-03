/* Execute uma vez no SQL Server como administrador e depois substitua o sa no worker. */
USE [master];
GO
CREATE LOGIN [contracts_reader] WITH PASSWORD = 'SUBSTITUA_POR_UMA_SENHA_FORTE_E_UNICA', CHECK_POLICY = ON;
GO
USE [smb001];
GO
CREATE USER [contracts_reader] FOR LOGIN [contracts_reader];
GO
GRANT SELECT ON dbo.MDCHP TO [contracts_reader];
GRANT SELECT ON dbo.MDCIP TO [contracts_reader];
GRANT SELECT ON dbo.MPreNota TO [contracts_reader];
GRANT SELECT ON dbo.MDCDP TO [contracts_reader];
GRANT SELECT ON dbo.MLANF TO [contracts_reader];
GRANT SELECT ON dbo.MExtratoBancoLanc TO [contracts_reader];
GRANT SELECT ON dbo.mlanc TO [contracts_reader];
GO
