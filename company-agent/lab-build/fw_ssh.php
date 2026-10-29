<?php
require_once("config.inc");
require_once("util.inc");
global $config;
if (!is_array($config['system']['ssh'])) $config['system']['ssh'] = array();
$config['system']['ssh']['enable'] = 'enabled';
$config['system']['ssh']['sshdkeyonly'] = 'enabled';
foreach ($config['system']['user'] as $i => $u) {
 if ($u['name'] == 'admin') $config['system']['user'][$i]['authorizedkeys'] = 'c3NoLWVkMjU1MTkgQUFBQUMzTnphQzFsWkRJMU5URTVBQUFBSURTN3pSNGJGL2N3cWl4RElxQlJIZ2FiYmc2RlVhVFBMMjdSVEpDUldlTFIgbGFiLWFnZW50QGFnZW50LXBj';
}
write_config('lab-agent ssh key');
echo "SSH-KEY-WRITTEN\n";
