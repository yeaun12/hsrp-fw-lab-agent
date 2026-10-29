# 랩 구축 기록 스크립트

HSRP-FW-LAB을 처음 만들 때 실제로 쓴 스크립트입니다. 랩을 **다시 켤 때는 상위 폴더의 `lab_up.py`만 쓰면 되고**, 이 폴더는 "어떻게 만들었는지"의 기록이자 처음부터 다시 만들 때의 참고용입니다. 일부 스크립트는 당시 작업 폴더와 프로젝트 ID를 전제로 합니다.

| 단계 | 파일 | 내용 |
|---|---|---|
| 공통 | `g.py` | GNS3 REST API + 노드 콘솔(telnet) 헬퍼 |
| 1단계 | `build.py`, `routers.py`, `fw_setup.php`, `web_setup.sh` | 9대 기본 랩: HSRP 코어(라우터 서브인터페이스), pfSense(WAN/LAN/DMZ, 룰), WEB 서버 |
| 2단계 | `build2.py`, `routers2.py`, `fw_setup2.php`, `srv_setup.sh` | 17대로 확장: L3 코어(SVI HSRP, Po1), ASW1/2, 지사 OSPF, ISP2 + 게이트웨이 그룹, 서버팜 ACL, kali |
| 3단계 | `build3.py`, `agentpc_setup.sh`, `mgmt_access.py`, `fw_ssh.php`, `fw_usersync.php` | AGENT-PC(관리망 + NAT), IOS 관리 계정·VTY ACL, pfSense SSH 키 |
| 4단계 | `build4.py`, `admin_shortcuts.cmd` | ADMIN-PC(Windows), 관리망 DHCP, 대시보드 바로가기 |
| 콘솔 조작 | `vbkey.py`, `fwser.py` | VM 화면에 키 입력·스크린샷, pfSense 시리얼 콘솔 |
| 게스트 실행 | `gexec.py`, `winexec.py`, `ash.py` | Guest Additions(Linux/Windows), AGENT-PC SSH |
| 검증 | `verify.py`, `failover.py`, `wanfail.py` | 명령 실행 결과 확인, CORE1 장애·ISP1 장애 시험 |
| 복구 | `reopen.py` | `gns3-agent/reopen_project.py`의 초기 버전 |

계정 정보는 들어 있지 않습니다. VM 비밀번호는 실행할 때 입력하거나 `gns3-agent/config.py`에서 읽고, 장비 관리 계정은 `../secrets.env`(git 제외)에 있습니다.
