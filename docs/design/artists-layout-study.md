# Estudo: lista e cards na página Artists

Proposta de apresentação, sem alterar o comportamento atual da aplicação. A análise considera a implementação publicada na PR #862 e compara três caminhos: conservar a lista, substituí-la por cards ou oferecer os dois modos.

## Recomendação

Manter a lista como padrão e oferecer **Lista / Cards** como preferência de visualização, ao lado dos filtros. Os dois modos devem usar o mesmo resultado, a mesma ordenação, a mesma paginação e os mesmos indicadores.

Assim, a navegação por imagens fica disponível sem impor uma mudança a quem prefere localizar nomes ou ainda tem poucos artistas com fotografias. Essa é uma recomendação de projeto, não uma conclusão de teste de usabilidade; a comparação visual deve orientar a escolha antes da implementação.

## O que já existe

- A apresentação atual usa itens horizontais com imagem de 80 × 80 px, nome, quantidade de álbuns e indicação textual de New. A grade tem uma, duas ou três colunas conforme a largura.
- A Library usa imagem quadrada, nome abaixo e badges em overlay. Sua grade varia de duas a cinco colunas.
- A lista de artistas já tem os dados necessários: MBID, nome, álbuns, caminhos de NFO, imagem resolvida e identificação de artistas com algum álbum adicionado nos últimos sete dias.
- Conflitos, órfãos, erros de leitura e álbuns sem IDs têm apresentações globais próprias.
- A paginação de artistas atualmente é de 30 itens. As imagens já têm carregamento preguiçoso, versão observada e revalidação por ETag.

Referências: [lista atual](../../templates/partials/artists_page.html), [filtros](../../templates/partials/_artist_filters.html), [card de álbum](../../templates/partials/library_tile.html), [modelo da coleção de artistas](../../src/harmonist/artists.py) e [inventário de NFOs](../../src/harmonist/artist_nfo.py).

## Comparação das alternativas

| Alternativa | Benefícios | Limitações | Adequação |
| --- | --- | --- | --- |
| Conservar a lista | Favorece leitura de nomes, acomoda nomes longos e funciona bem sem fotografias. Menor mudança na interface. | A imagem tem menos destaque; o resultado fica visualmente diferente da Library. | Consulta por nome e revisão de metadados. |
| Usar somente cards | Aproxima a identidade visual da Library; dá destaque à fotografia e aos indicadores sobre a imagem. | Fotos pequenas podem perder definição. Recortes podem esconder integrantes de uma banda. Muitos placeholders ficam mais evidentes. | Navegação visual, especialmente com boa cobertura de imagens. |
| Oferecer Lista e Cards | Preserva a opção existente e permite escolher conforme a coleção e a tarefa. | Exige preservar a preferência e verificar os dois layouts em filtros, paginação e atualizações automáticas. | Melhor equilíbrio para a proposta atual. |

A densidade depende da largura: a lista tem menos colunas, mas itens menores; os cards têm mais colunas e imagens maiores. Não é correto afirmar que um modo sempre mostra mais artistas por tela sem medir o resultado nas larguras relevantes.

## Cards no padrão da Library

Cada card teria uma área quadrada para a imagem do artista, seu nome abaixo e, em outra linha cinza, `1 album` ou `N albums`. O número continua representando os álbuns disponíveis na Library; a discografia declarada no NFO não entra nessa contagem.

A grade pode seguir os mesmos breakpoints da Library: duas colunas no celular, três em telas pequenas, quatro em telas grandes e cinco nas maiores. O nome pode ocupar duas linhas, com espaço reservado para manter os contadores alinhados. Nomes completos precisam continuar acessíveis.

A imagem existente é reutilizada. A apresentação não deve procurar imagens novas, fazer consultas ao MusicBrainz ou mudar as regras de seleção de `<thumb>`. Sem imagem válida, o card mantém a mesma área com um placeholder neutro, sem substituir a foto pela capa de um álbum.

O recorte quadrado com `object-fit: cover` aproxima o resultado da Library, mas precisa ser conferido com retratos e fotos de bandas. `contain` preserva a imagem inteira, com margens; é uma alternativa visual a comparar. Não há necessidade de introduzir detecção de rostos, processamento de imagens ou uma configuração por artista para esta etapa.

## Badges e o significado de cada um

O estilo dos badges pode ser compartilhado com a Library, mas seus significados devem continuar específicos de artistas.

| Badge proposto | Condição | Apresentação sugerida |
| --- | --- | --- |
| New | Ao menos um álbum da Library foi adicionado nos últimos sete dias. | Canto superior direito, usando a convenção visual já existente para New. |
| NFO conflict | Mais de um NFO válido declara o mesmo MBID. | Área inferior, com prioridade entre os avisos. |
| Missing NFO | O inventário concluído não encontrou NFO associado ao MBID. | Área inferior. |
| Missing artwork | Não foi possível resolver uma imagem local válida, com inventário disponível. | Área inferior ou junto dos metadados na lista. |

Não acrescentaria um badge de sucesso em todo artista que possui NFO ou imagem: isso produz ruído sem ajudar a decidir o que consultar. Os filtros de presença já cobrem essa consulta.

Um conflito pode impedir a escolha da imagem. Nesse caso, mostrar primeiro o conflito evita apresentar a falta de imagem como um segundo problema independente. Se houver leitura incompleta ou erro de varredura, não se deve transformar informação desconhecida em Missing NFO ou Missing artwork.

Os badges devem ter texto legível e fundo opaco para funcionar sobre fotografias claras ou escuras. Eles precisam se reorganizar em mais de uma linha em telas estreitas, sem cobrir o nome, que permanece abaixo da imagem. O primeiro incremento pode mantê-los informativos, com o card inteiro abrindo `/artist/{mbid}`; não há necessidade de aninhar links ou botões nos badges.

Na lista, os mesmos indicadores podem ficar próximos ao contador de álbuns, preservando a fotografia pequena. A diferença entre os modos deve ser de disposição, não de informação.

Órfãos e álbuns sem IDs continuam na área global: eles não têm uma associação válida com um card de artista. Os caminhos de conflitos e os erros de leitura também continuam acessíveis nas apresentações existentes, mesmo quando um badge resume o problema.

## Onde colocar a opção de visualização

Usaria um seletor visível **Visualização: Lista | Cards**, próximo à linha de filtros. No desktop, ele pode ficar à direita; no celular, em uma linha própria.

Embora possa ocupar a mesma área da tela, visualização não é um filtro: mudar o modo não deve alterar quais artistas aparecem, a contagem, a ordenação ou a página atual. `Clear filters` também não deve apagar a preferência de visualização. Colocar Cards dentro do menu NFO ou Artwork misturaria decisões diferentes.

Se o espaço exigir um menu, a alternativa seria um menu próprio chamado **View**, com Lista e Cards. O seletor visível é mais fácil de descobrir para apenas duas opções.

## Preferência, URLs e retorno da página do artista

Uma implementação coerente com a Library usaria a precedência:

1. `view=list` ou `view=cards` explícito na URL.
2. Preferência válida lembrada em um cookie exclusivo de Artists.
3. Lista como padrão.

Exemplo: `/artists?view=cards&nfo=missing&artist_q=arctic`.

Só uma escolha explícita deve atualizar o cookie. Como o servidor renderiza a primeira página, esse padrão evita renderizar um layout e trocá-lo depois com JavaScript. Valores inválidos voltam a um padrão seguro.

O modo resolvido precisa acompanhar busca, filtros, paginação, refresh do watcher e retorno da página do artista, inclusive numa passagem por um álbum. A preferência não pertence ao Profile, ao NFO ou ao banco de dados: ela descreve apenas como o navegador apresenta a coleção.

Inicialmente, manteria 30 artistas por página nos dois modos. Assim, a troca mantém a página e os mesmos artistas. Uma opção de quantidade por página pode ser estudada separadamente, sem reutilizar o cookie da Library e acoplar duas preferências diferentes.

## Esforço e organização da implementação

A maior parte dos dados já existe. A mudança principal ficaria na composição dos templates, na preferência de visualização e na propagação dos parâmetros. Não exige outro inventário, leitura adicional dos NFOs ou consulta por artista ao filesystem durante a renderização.

Uma organização possível seria extrair componentes compartilhados para imagem, indicadores e identificação do artista, usados por uma apresentação horizontal e outra vertical. A seleção dos artistas e os significados dos badges permaneceriam centralizados para evitar divergência entre os layouts.

Há uma diferença relevante entre ter zero caminhos de NFO confirmados e não conseguir ler o inventário: os badges precisam consumir essa evidência, sem inferir ausência a partir de uma falha. Estados não devem ser persistidos como novos flags no Profile.

A opção híbrida tem esforço maior que uma troca exclusivamente visual porque os parâmetros precisam atravessar todos os percursos. Ainda assim, continua limitada à apresentação e às preferências do navegador, sem depender da futura criação ou edição de NFOs.

## Como validar antes de adotar

- Comparar os mesmos artistas nos dois modos, com imagem, sem imagem, nome longo, colaboração, conflito, NFO ausente e New.
- Conferir largura de 320–390 px, tablet e desktop; testar fotos de bandas, retratos e imagens pequenas.
- Confirmar igualdade de resultados, contagens e ordenação nos dois modos, e preservar os 30 itens por página ao alternar.
- Abrir o artista, abrir um álbum e voltar; preservar modo, busca, filtros e paginação.
- Verificar que o watcher atualiza badges e imagens sem trocar o modo escolhido ou perder o foco indevidamente.
- Usar controles nativos acessíveis por teclado e rótulos claros, sem depender apenas de cor ou de hover para explicar um aviso.
- Medir a experiência com muitas imagens ausentes e com arquivos grandes. Reutilizar `loading="lazy"`, os URLs versionados e as respostas 304 já existentes, sem prometer ganho de desempenho antes de medir.
- Conferir Chromium e WebKit, regenerar o CSS e executar os checks e testes de navegador apropriados.

Este estudo não altera a página publicada, não modifica a PR #862 e não publica uma nova decisão visual no GitHub.
