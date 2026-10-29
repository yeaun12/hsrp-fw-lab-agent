<?php
require_once("config.inc");
require_once("util.inc");
global $config;
$any = array('any' => '');
$inside = array('address' => '10.0.0.0/15');
$config['interfaces']['opt2'] = array('enable' => '', 'if' => 'em3', 'descr' => 'WAN2', 'ipaddr' => '211.0.2.2', 'subnet' => '30', 'gateway' => 'WAN2GW');
$config['gateways'] = array('defaultgw4' => 'WANFAIL', 'gateway_item' => array(
 array('interface' => 'wan', 'gateway' => '211.0.0.1', 'name' => 'WANGW', 'weight' => '1', 'ipprotocol' => 'inet', 'descr' => 'ISP1'),
 array('interface' => 'opt2', 'gateway' => '211.0.2.1', 'name' => 'WAN2GW', 'weight' => '1', 'ipprotocol' => 'inet', 'descr' => 'ISP2'),
 array('interface' => 'lan', 'gateway' => '10.0.99.254', 'name' => 'HSRPGW', 'weight' => '1', 'ipprotocol' => 'inet', 'descr' => 'CORE HSRP VIP')),
 'gateway_group' => array(array('name' => 'WANFAIL', 'item' => array('WANGW|1|address', 'WAN2GW|2|address'), 'trigger' => 'down', 'descr' => 'ISP1 primary, ISP2 backup')));
$routes = array();
foreach (array('10.0.10.0/24', '10.0.20.0/24', '10.0.30.0/24', '10.0.40.0/24', '10.1.0.0/16') as $net)
 $routes[] = array('network' => $net, 'gateway' => 'HSRPGW', 'descr' => 'inside');
$config['staticroutes'] = array('route' => $routes);
$config['filter']['rule'] = array(
 array('type' => 'pass', 'interface' => 'lan', 'ipprotocol' => 'inet', 'source' => $inside, 'destination' => array('network' => 'opt1'), 'descr' => 'INSIDE to DMZ (no policy routing)', 'tracker' => '1000000100'),
 array('type' => 'pass', 'interface' => 'lan', 'ipprotocol' => 'inet', 'source' => $inside, 'destination' => $any, 'gateway' => 'WANFAIL', 'descr' => 'INSIDE to any (ISP failover)', 'tracker' => '1000000101'),
 array('type' => 'pass', 'interface' => 'wan', 'ipprotocol' => 'inet', 'protocol' => 'tcp', 'source' => $any, 'destination' => array('address' => '211.0.1.10', 'port' => '80'), 'descr' => 'WEB http', 'tracker' => '1000000102'),
 array('type' => 'pass', 'interface' => 'wan', 'ipprotocol' => 'inet', 'protocol' => 'tcp', 'source' => $any, 'destination' => array('address' => '211.0.1.10', 'port' => '443'), 'descr' => 'WEB https', 'tracker' => '1000000103'),
 array('type' => 'block', 'interface' => 'opt1', 'ipprotocol' => 'inet', 'source' => array('network' => 'opt1'), 'destination' => $inside, 'descr' => 'DMZ to INSIDE block', 'tracker' => '1000000104'),
 array('type' => 'pass', 'interface' => 'opt1', 'ipprotocol' => 'inet', 'source' => array('network' => 'opt1'), 'destination' => $any, 'gateway' => 'WANFAIL', 'descr' => 'DMZ to internet', 'tracker' => '1000000105'));
write_config('HSRP-FW-LAB phase 2');
require_once("filter.inc");
filter_configure_sync();
echo "CONFIG2-WRITTEN\n";
