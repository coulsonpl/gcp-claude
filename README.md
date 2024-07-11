### 运行容器
运行容器，可以通过环境变量传递配置信息，例如代理URL和Token。以下是一个示例：

```yaml
version: '3'
services:
  gcp-claude:
    container_name: gcp-claude
    image: coulsontl/gcp-claude:latest
    restart: always
    network_mode: bridge
    ports:
      - "3001:3000"
    environment:
      - API_KEY=sk-xxxx
      - HTTP_PROXY=http://host:prot
      - HTTPS_PROXY=http://host:prot
      - ACCOUNT_GCPA={"PROJECT_ID":"xxxx","CLIENT_ID":"xxx","CLIENT_SECRET":"xxx","REFRESH_TOKEN":"xxxx"}
      - ACCOUNT_GCPB={"PROJECT_ID":"xxxx","CLIENT_ID":"xxx","CLIENT_SECRET":"xxx","REFRESH_TOKEN":"xxxx"}
```

### 环境变量说明
* HTTP_PROXY: 指定所有请求通过的代理服务器的URL
* ACCOUNT_XX: 账号信息参考 https://linux.do/t/topic/118702#gcp-1
* API_KEY: 调用接口时的鉴权Token
