# Reversão mínima da navegação de Conciliações

## Objetivo

Restaurar a experiência anterior da rota `/conciliacoes`: a página deve abrir diretamente a Fila de conciliação, sem o seletor visual com Fila, Exceções, Competências e Cobertura automática.

## Escopo

- Remover somente o seletor de quatro visões e o estado de aba associado.
- Restaurar o cabeçalho anterior: título `Fila de conciliação` e subtítulo `Comece pelo que precisa de ação. Os confirmados ficam no histórico, sem esconder a rastreabilidade.`
- Manter a fila operacional atual, seus filtros, parâmetros de URL, estados, paginação, tratamento de erros e drawer de conferência.
- Manter os componentes e endpoints de Exceções, Competências e Cobertura automática no código, sem expô-los nessa rota.
- Remover os estilos exclusivos do seletor quando não houver outro consumidor.

## Fora do escopo

- Alterar regras de conciliação, contagens, severidade, filtros ou endpoints.
- Excluir componentes ou APIs das três visões retiradas da navegação.
- Criar feature flag, nova rota ou novo item no menu lateral.
- Publicar a alteração em produção.

## Comportamento esperado

Ao acessar `/conciliacoes`, o usuário vê imediatamente o cabeçalho e o conteúdo da Fila de conciliação. Não existem botões com os nomes `Fila`, `Exceções`, `Competências` ou `Cobertura automática` no seletor superior. Filtros recebidos pela URL continuam sendo aplicados à fila.

## Testes

- Um teste de componente deve confirmar o cabeçalho `Fila de conciliação`.
- O mesmo teste deve confirmar a ausência do seletor e das três visões adicionais.
- Os testes existentes da fila devem continuar aprovados.
- A suíte frontend completa e o build Vite devem finalizar sem erros.
