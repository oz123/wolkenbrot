# Wolkenbrot

## bakes and manages your AWS cloud images

wolkenbrot is named after a German children's title called Wolkenbrot by
the Korean authors Baek Hee Na Kim Hyang Soo. The translation to English is
cloud's bread.

Wolken brot is inspired and packer[1] kujenga[2], removing fabric as a
dependency. It also aims to be more tested and documented.

In case you woder, yes it's similar to packer by Hashicorp.
But here are some reasons that you might like it better than packer:

1. It's written in Python.
2. It's not written in Go.
3. It can use private AMI as a starting point for your build.
4. It's smaller and easier to hack on.
5. It has a cooler name.
6. It has a better license, MIT sounds way better then MPL-2. Especially because it means "with" in German.

### how is it different from Packer or kujenga?

1. It is Python3 only.
2. It does not depend on fabric.
3. It replaces boto with boto3
4. It's name is not Swahili, but rather German.
5. It adds the ability to list your images
6. It adds the ability to get detailed information about an image from the CLI.
7. It adds the ability to de-register images from the CLI.

[1]: https://github.com/macd/kujenga
[2]: https://www.packer.io/

### USAGE

You can run the following command to build an image:

```
    $ wolkenbrot bake <image.json>
```

See the documentation for the JSON format.

You can run the following command to list your existing images:

```
    $ wolkenbrot list
```

You can view the information about and image:

```
	 $  wolkenbrot info ami-72192e
```

You can remove the image with:

```
	 $ wolkenbrot delete ami-72192e
```

Wolkenbrot follows boto3 configuration principles, so if you wonder how to
pass AWS configuration parameters, take a look in [Boto3's own documentation][2]

[3]: http://boto3.readthedocs.io/en/latest/guide/configuration.html

### FAQ

 * Do you support Windows or Mac OS X?

   The author of this software strongly despises working on Windows or
	 Mac OS X. Hence, this software is not tested for these platforms.
	 If you can run Python on your OS, it might run.

 * Do you support provisioning machines with Saltstack\Chef\Puppet\Ansible\XYZ?

  Strictly speaking **Nope**. Alas, see the answer bellow.

 * How can I ,never the less, provision my machine with saltstack?

 One of the annoyances I have had with packer, is that it always provisions salt
 on the image builder before actually running my salt states.
 That is, by default, Packer only uses publicly available images as the starting point.
 If you plan to use salt, simply create an image which already contains salt installed,
 and add a single provisioning command `salt-call` after uploading your states to the destination machine
 If other systems are able to do something like that (I believe ansible can run playbooks locally too) then
 you can use any system you like.


### Testing and Installing the test requirements

Simply issue:

```
   $ pip install -e ".[testing]"
	 $ make test
```
