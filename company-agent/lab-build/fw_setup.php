<?php
require_once("config.inc");
require_once("util.inc");
global $config;
$any = array('any' => '');
$config['system']['hostname'] = 'FW';
$config['system']['enableserial'] = '';
$config['system']['serialspeed'] = '115200';
$config['system']['primaryconsole'] = 'serial';
$config['interfaces']['wan'] = array('enable' => '', 'if' => 'em0', 'descr' => 'WAN', 'ipaddr' => '211.0.0.2', 'subnet' => '30', 'gateway' => 'WANGW');
$config['interfaces']['lan'] = array('enable' => '', 'if' => 'em1', 'descr' => 'LAN', 'ipaddr' => '10.0.99.1', 'subnet' => '24');
$config['interfaces']['opt1'] = array('enable' => '', 'if' => 'em2', 'descr' => 'DMZ', 'ipaddr' => '211.0.1.1', 'subnet' => '28');
unset($config['dhcpd']);
$config['gateways'] = array('defaultgw4' => 'WANGW', 'gateway_item' => array(
 array('interface' => 'wan', 'gateway' => '211.0.0.1', 'name' => 'WANGW', 'weight' => '1', 'ipprotocol' => 'inet', 'descr' => 'ISP'),
 array('interface' => 'lan', 'gateway' => '10.0.99.254', 'name' => 'HSRPGW', 'weight' => '1', 'ipprotocol' => 'inet', 'descr' => 'CORE HSRP VIP')));
$config['staticroutes'] = array('route' => array(
 array('network' => '10.0.10.0/24', 'gateway' => 'HSRPGW', 'descr' => 'VLAN10'),
 array('network' => '10.0.20.0/24', 'gateway' => 'HSRPGW', 'descr' => 'VLAN20')));
if (!is_array($config['nat'])) $config['nat'] = array();
$config['nat']['outbound'] = array('mode' => 'automatic');
if (!is_array($config['filter'])) $config['filter'] = array();
$config['filter']['rule'] = array(
 array('type' => 'pass', 'interface' => 'lan', 'ipprotocol' => 'inet', 'source' => array('address' => '10.0.0.0/16'), 'destination' => $any, 'descr' => 'INSIDE to any', 'tracker' => '1000000101'),
 array('type' => 'pass', 'interface' => 'wan', 'ipprotocol' => 'inet', 'protocol' => 'tcp', 'source' => $any, 'destination' => array('address' => '211.0.1.10', 'port' => '80'), 'descr' => 'WEB http', 'tracker' => '1000000102'),
 array('type' => 'pass', 'interface' => 'wan', 'ipprotocol' => 'inet', 'protocol' => 'tcp', 'source' => $any, 'destination' => array('address' => '211.0.1.10', 'port' => '443'), 'descr' => 'WEB https', 'tracker' => '1000000103'),
 array('type' => 'block', 'interface' => 'opt1', 'ipprotocol' => 'inet', 'source' => array('network' => 'opt1'), 'destination' => array('address' => '10.0.0.0/16'), 'descr' => 'DMZ to INSIDE block', 'tracker' => '1000000104'),
 array('type' => 'pass', 'interface' => 'opt1', 'ipprotocol' => 'inet', 'source' => array('network' => 'opt1'), 'destination' => $any, 'descr' => 'DMZ to internet', 'tracker' => '1000000105'));
write_config('HSRP-FW-LAB setup');
echo "CONFIG-WRITTEN\n";
